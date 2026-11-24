create table if not exists public.sync_records (
    id uuid primary key,
    entity_type text not null check (
        entity_type in (
            'warehouses', 'racks', 'levels', 'boxes', 'articles', 'barcodes',
            'movements', 'dmx_modes', 'dmx_channels', 'repairs', 'scans',
            'inventories', 'inventory_items'
        )
    ),
    data jsonb,
    revision bigint not null default 1,
    deleted boolean not null default false,
    updated_at timestamptz not null default now(),
    constraint sync_records_data_check check (deleted or data is not null)
);

alter table public.sync_records enable row level security;
revoke all on public.sync_records from public, anon, authenticated;
grant select, insert, update, delete on public.sync_records to service_role;

create or replace function public.eventtech_apply_sync(p_changes jsonb)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    change_item jsonb;
    current_record public.sync_records%rowtype;
    conflicting_id uuid;
    entity_id uuid;
    entity_type_value text;
    expected_revision_value bigint;
    next_revision bigint;
    deleted_value boolean;
    barcode_value text;
    conflicts jsonb := '[]'::jsonb;
begin
    if jsonb_typeof(p_changes) <> 'array' or jsonb_array_length(p_changes) > 200 then
        raise exception 'Invalid sync batch';
    end if;

    perform pg_advisory_xact_lock(1701199117);

    for change_item in select value from jsonb_array_elements(p_changes)
    loop
        entity_id := (change_item->>'id')::uuid;
        entity_type_value := change_item->>'entity_type';
        deleted_value := coalesce((change_item->>'deleted')::boolean, false);
        expected_revision_value := case
            when not (change_item ? 'expected_revision')
                 or jsonb_typeof(change_item->'expected_revision') = 'null' then null
            else (change_item->>'expected_revision')::bigint
        end;

        if entity_type_value is null or entity_type_value not in (
            'warehouses', 'racks', 'levels', 'boxes', 'articles', 'barcodes',
            'movements', 'dmx_modes', 'dmx_channels', 'repairs', 'scans',
            'inventories', 'inventory_items'
        ) then
            raise exception 'Invalid entity type';
        end if;

        select * into current_record
        from public.sync_records
        where id = entity_id
        for update;

        if found then
            if expected_revision_value is null
               or expected_revision_value <> current_record.revision then
                conflicts := conflicts || jsonb_build_array(jsonb_build_object(
                    'id', entity_id,
                    'reason', 'revision',
                    'record', to_jsonb(current_record)
                ));
                continue;
            end if;
        elsif expected_revision_value is not null then
            conflicts := conflicts || jsonb_build_array(jsonb_build_object(
                'id', entity_id,
                'reason', 'missing'
            ));
            continue;
        end if;

        if not deleted_value and jsonb_typeof(change_item->'data') <> 'object' then
            raise exception 'Invalid sync record data';
        end if;

        barcode_value := nullif(change_item->'data'->>'barcode', '');
        if not deleted_value and barcode_value is not null
           and entity_type_value in ('warehouses', 'racks', 'levels', 'boxes', 'articles') then
            select id into conflicting_id
            from public.sync_records
            where deleted = false
              and entity_type in ('warehouses', 'racks', 'levels', 'boxes', 'articles')
              and id <> entity_id
              and data->>'barcode' = barcode_value
            limit 1;
            if conflicting_id is not null then
                conflicts := conflicts || jsonb_build_array(jsonb_build_object(
                    'id', entity_id,
                    'reason', 'duplicate_barcode',
                    'duplicate_id', conflicting_id
                ));
                continue;
            end if;
        end if;

        next_revision := coalesce(current_record.revision, 0) + 1;
        insert into public.sync_records(id, entity_type, data, revision, deleted, updated_at)
        values (
            entity_id,
            entity_type_value,
            case when deleted_value then null else change_item->'data' end,
            next_revision,
            deleted_value,
            now()
        )
        on conflict (id) do update set
            entity_type = excluded.entity_type,
            data = excluded.data,
            revision = excluded.revision,
            deleted = excluded.deleted,
            updated_at = excluded.updated_at;
    end loop;

    return jsonb_build_object('conflicts', conflicts);
end;
$$;

revoke all on function public.eventtech_apply_sync(jsonb) from public, anon, authenticated;
grant execute on function public.eventtech_apply_sync(jsonb) to service_role;
