alter table public.sync_records
    drop constraint if exists sync_records_entity_type_check;

alter table public.sync_records
    add constraint sync_records_entity_type_check check (
        entity_type in (
            'warehouses', 'racks', 'levels', 'boxes', 'articles', 'barcodes',
            'movements', 'dmx_modes', 'dmx_channels', 'repairs', 'scans',
            'inventories', 'inventory_items', 'events', 'event_items'
        )
    );

create or replace function public.eventtech_validate_event_reservation(
    p_event_id uuid,
    p_event_item_id uuid,
    p_event_data jsonb,
    p_article_id uuid,
    p_required_quantity integer
)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    article_data jsonb;
    article_quantity integer;
    reserved_quantity integer;
    start_value date;
    end_value date;
begin
    if p_event_data->>'status' not in (
        'Geplant', 'Vorbereitung', 'Bereit zur Ausgabe', 'Läuft',
        'Rückgabe ausstehend', 'Abgeschlossen', 'Abgesagt'
    ) then
        raise exception 'Invalid event status' using errcode = '23514';
    end if;
    if p_required_quantity is null or p_required_quantity < 1 then
        raise exception 'Event quantity must be positive' using errcode = '23514';
    end if;
    if nullif(p_event_data->>'name', '') is null then
        raise exception 'Event name is required' using errcode = '23514';
    end if;

    begin
        start_value := (p_event_data->>'start_date')::date;
        end_value := (p_event_data->>'end_date')::date;
    exception when others then
        raise exception 'Event dates are invalid' using errcode = '23514';
    end;
    if start_value is null or end_value is null or end_value < start_value then
        raise exception 'Event end date precedes start date' using errcode = '23514';
    end if;

    select data into article_data
    from public.sync_records
    where id = p_article_id
      and entity_type = 'articles'
      and deleted = false;
    if not found then
        raise exception 'Event article does not exist' using errcode = '23503';
    end if;
    begin
        article_quantity := greatest(0, coalesce((article_data->>'quantity')::integer, 0));
    exception when others then
        raise exception 'Article quantity is invalid' using errcode = '23514';
    end;

    if exists (
        select 1
        from public.sync_records existing_item
        where existing_item.entity_type = 'event_items'
          and existing_item.deleted = false
          and existing_item.id <> p_event_item_id
          and existing_item.data->>'event_id' = p_event_id::text
          and existing_item.data->>'article_id' = p_article_id::text
    ) then
        raise exception 'Article is already assigned to this event' using errcode = '23505';
    end if;
    if p_event_data->>'status' in ('Abgeschlossen', 'Abgesagt') then
        return;
    end if;

    select coalesce(sum((other_item.data->>'required_quantity')::integer), 0)::integer
    into reserved_quantity
    from public.sync_records other_item
    join public.sync_records other_event
      on other_event.id = (other_item.data->>'event_id')::uuid
     and other_event.entity_type = 'events'
     and other_event.deleted = false
    where other_item.entity_type = 'event_items'
      and other_item.deleted = false
      and other_item.data->>'article_id' = p_article_id::text
      and other_event.id <> p_event_id
      and other_event.data->>'status' not in ('Abgeschlossen', 'Abgesagt')
      and (other_event.data->>'start_date')::date <= end_value
      and (other_event.data->>'end_date')::date >= start_value;

    if p_required_quantity > article_quantity - reserved_quantity then
        raise exception 'Event reservation conflict: requested %, available %',
            p_required_quantity, greatest(0, article_quantity - reserved_quantity)
            using errcode = '23514';
    end if;
end;
$$;

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
    event_data jsonb;
    event_uuid uuid;
    article_uuid uuid;
    event_start date;
    event_end date;
    required_quantity_value integer;
    issued_quantity_value integer;
    returned_quantity_value integer;
    missing_quantity_value integer;
    damaged_quantity_value integer;
    related_item record;
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
            'inventories', 'inventory_items', 'events', 'event_items'
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

        if deleted_value and entity_type_value = 'events' and exists (
            select 1
            from public.sync_records
            where entity_type = 'event_items'
              and deleted = false
              and data->>'event_id' = entity_id::text
        ) then
            raise exception 'Event assignments must be removed before deleting an event'
                using errcode = '23503';
        end if;

        if not deleted_value and entity_type_value = 'events' then
            if nullif(change_item->'data'->>'name', '') is null
               or coalesce(change_item->'data'->>'status', '') not in (
                   'Geplant', 'Vorbereitung', 'Bereit zur Ausgabe', 'Läuft',
                   'Rückgabe ausstehend', 'Abgeschlossen', 'Abgesagt'
               ) then
                raise exception 'Invalid event data' using errcode = '23514';
            end if;
            begin
                event_start := (change_item->'data'->>'start_date')::date;
                event_end := (change_item->'data'->>'end_date')::date;
            exception when others then
                raise exception 'Event dates are invalid' using errcode = '23514';
            end;
            if event_start is null or event_end is null or event_end < event_start then
                raise exception 'Event end date precedes start date' using errcode = '23514';
            end if;
            if change_item->'data'->>'status' in ('Abgeschlossen', 'Abgesagt')
               and exists (
                   select 1
                   from public.sync_records
                   where entity_type = 'event_items'
                     and deleted = false
                     and data->>'event_id' = entity_id::text
                     and coalesce((data->>'issued_quantity')::integer, 0)
                         > coalesce((data->>'returned_quantity')::integer, 0)
                           + coalesce((data->>'missing_quantity')::integer, 0)
               ) then
                raise exception 'Event has outstanding issued items' using errcode = '23514';
            end if;
            for related_item in
                select id, data
                from public.sync_records
                where entity_type = 'event_items'
                  and deleted = false
                  and data->>'event_id' = entity_id::text
            loop
                perform public.eventtech_validate_event_reservation(
                    entity_id,
                    related_item.id,
                    change_item->'data',
                    (related_item.data->>'article_id')::uuid,
                    (related_item.data->>'required_quantity')::integer
                );
            end loop;
        end if;

        if not deleted_value and entity_type_value = 'event_items' then
            begin
                event_uuid := (change_item->'data'->>'event_id')::uuid;
                article_uuid := (change_item->'data'->>'article_id')::uuid;
                required_quantity_value := (change_item->'data'->>'required_quantity')::integer;
                issued_quantity_value := coalesce((change_item->'data'->>'issued_quantity')::integer, 0);
                returned_quantity_value := coalesce((change_item->'data'->>'returned_quantity')::integer, 0);
                missing_quantity_value := coalesce((change_item->'data'->>'missing_quantity')::integer, 0);
                damaged_quantity_value := coalesce((change_item->'data'->>'damaged_quantity')::integer, 0);
            exception when others then
                raise exception 'Invalid event assignment data' using errcode = '23514';
            end;
            if issued_quantity_value < 0
               or returned_quantity_value < 0
               or missing_quantity_value < 0
               or damaged_quantity_value < 0
               or issued_quantity_value > required_quantity_value
               or returned_quantity_value + missing_quantity_value > issued_quantity_value
               or damaged_quantity_value > returned_quantity_value then
                raise exception 'Invalid event issue or return quantities'
                    using errcode = '23514';
            end if;
            select data into event_data
            from public.sync_records
            where id = event_uuid
              and entity_type = 'events'
              and deleted = false;
            if not found then
                raise exception 'Event does not exist' using errcode = '23503';
            end if;
            perform public.eventtech_validate_event_reservation(
                event_uuid, entity_id, event_data, article_uuid, required_quantity_value
            );
        end if;

        if not deleted_value and entity_type_value = 'articles' then
            for related_item in
                select item.id, item.data, event.data as event_data
                from public.sync_records item
                join public.sync_records event
                  on event.id = (item.data->>'event_id')::uuid
                 and event.entity_type = 'events'
                 and event.deleted = false
                where item.entity_type = 'event_items'
                  and item.deleted = false
                  and item.data->>'article_id' = entity_id::text
            loop
                perform public.eventtech_validate_event_reservation(
                    (related_item.data->>'event_id')::uuid,
                    related_item.id,
                    related_item.event_data,
                    entity_id,
                    (related_item.data->>'required_quantity')::integer
                );
            end loop;
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
revoke all on function public.eventtech_validate_event_reservation(uuid, uuid, jsonb, uuid, integer)
    from public, anon, authenticated;
