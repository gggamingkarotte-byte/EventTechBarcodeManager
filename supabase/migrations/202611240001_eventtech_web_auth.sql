grant usage on schema public to authenticated;
grant select on public.sync_records to authenticated;

drop policy if exists "Authenticated users can read inventory sync records"
    on public.sync_records;
create policy "Authenticated users can read inventory sync records"
    on public.sync_records
    for select
    to authenticated
    using (auth.uid() is not null);

create or replace function public.eventtech_web_apply_sync(p_changes jsonb)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    change_item jsonb;
    entity_id uuid;
    expected_revision_value bigint;
    current_revision bigint;
    sync_result jsonb;
begin
    if auth.uid() is null then
        raise exception 'Authentication required' using errcode = '42501';
    end if;

    if jsonb_typeof(p_changes) <> 'array' or jsonb_array_length(p_changes) > 200 then
        raise exception 'Invalid sync batch';
    end if;

    perform pg_advisory_xact_lock(1701199117);

    for change_item in select value from jsonb_array_elements(p_changes)
    loop
        entity_id := (change_item->>'id')::uuid;
        expected_revision_value := case
            when not (change_item ? 'expected_revision')
                 or jsonb_typeof(change_item->'expected_revision') = 'null' then null
            else (change_item->>'expected_revision')::bigint
        end;

        select revision into current_revision
        from public.sync_records
        where id = entity_id
        for update;

        if found then
            if expected_revision_value is null
               or expected_revision_value <> current_revision then
                raise exception 'Sync revision conflict' using errcode = '40001';
            end if;
        elsif expected_revision_value is not null then
            raise exception 'Sync record missing' using errcode = '40001';
        end if;
    end loop;

    sync_result := public.eventtech_apply_sync(p_changes);
    if jsonb_array_length(sync_result->'conflicts') > 0 then
        raise exception 'Sync conflict' using errcode = '40001';
    end if;
    return sync_result;
end;
$$;

revoke all on function public.eventtech_web_apply_sync(jsonb) from public, anon;
grant execute on function public.eventtech_web_apply_sync(jsonb) to authenticated;
