create table if not exists public.eventtech_web_profiles (
    user_id uuid primary key references auth.users(id) on delete cascade,
    status text not null default 'pending'
        check (status in ('pending', 'approved', 'denied')),
    role text not null default 'user'
        check (role in ('user', 'admin')),
    created_at timestamptz not null default now()
);

alter table public.eventtech_web_profiles enable row level security;
revoke all on public.eventtech_web_profiles from public, anon, authenticated;
grant select on public.eventtech_web_profiles to authenticated;

drop policy if exists "Users can read their own web profile"
    on public.eventtech_web_profiles;
create policy "Users can read their own web profile"
    on public.eventtech_web_profiles
    for select
    to authenticated
    using (user_id = auth.uid());

insert into public.eventtech_web_profiles(user_id)
select id
from auth.users
on conflict (user_id) do nothing;

create or replace function public.eventtech_web_create_profile()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
    insert into public.eventtech_web_profiles(user_id)
    values (new.id)
    on conflict (user_id) do nothing;
    return new;
end;
$$;

revoke all on function public.eventtech_web_create_profile() from public, anon, authenticated;
drop trigger if exists eventtech_web_create_profile on auth.users;
create trigger eventtech_web_create_profile
    after insert on auth.users
    for each row execute function public.eventtech_web_create_profile();

create or replace function public.eventtech_web_is_approved()
returns boolean
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    select exists (
        select 1
        from public.eventtech_web_profiles
        where user_id = auth.uid()
          and status = 'approved'
    );
$$;

create or replace function public.eventtech_web_is_admin()
returns boolean
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    select exists (
        select 1
        from public.eventtech_web_profiles
        where user_id = auth.uid()
          and status = 'approved'
          and role = 'admin'
    );
$$;

revoke all on function public.eventtech_web_is_approved() from public, anon, authenticated;
revoke all on function public.eventtech_web_is_admin() from public, anon, authenticated;
grant execute on function public.eventtech_web_is_approved() to authenticated;
grant execute on function public.eventtech_web_is_admin() to authenticated;

drop policy if exists "Authenticated users can read inventory sync records"
    on public.sync_records;
create policy "Approved users can read inventory sync records"
    on public.sync_records
    for select
    to authenticated
    using (public.eventtech_web_is_approved());

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
    if not public.eventtech_web_is_approved() then
        raise exception 'Approved account required' using errcode = '42501';
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

create or replace function public.eventtech_web_list_pending_users()
returns table(user_id uuid, email text, created_at timestamptz)
language plpgsql
security definer
set search_path = public, auth, pg_temp
as $$
begin
    if not public.eventtech_web_is_admin() then
        raise exception 'Administrator access required' using errcode = '42501';
    end if;

    return query
    select users.id, users.email::text, users.created_at
    from auth.users as users
    join public.eventtech_web_profiles as profiles on profiles.user_id = users.id
    where profiles.status = 'pending'
      and profiles.role = 'user'
    order by users.created_at;
end;
$$;

create or replace function public.eventtech_web_set_user_approval(
    p_user_id uuid,
    p_approved boolean
)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
    if not public.eventtech_web_is_admin() then
        raise exception 'Administrator access required' using errcode = '42501';
    end if;
    if p_user_id = auth.uid() then
        raise exception 'Administrators cannot change their own approval' using errcode = '42501';
    end if;

    update public.eventtech_web_profiles
    set status = case when p_approved then 'approved' else 'denied' end
    where user_id = p_user_id
      and role = 'user'
      and status = 'pending';

    if not found then
        raise exception 'Pending user not found';
    end if;
end;
$$;

revoke all on function public.eventtech_web_list_pending_users() from public, anon, authenticated;
revoke all on function public.eventtech_web_set_user_approval(uuid, boolean) from public, anon, authenticated;
grant execute on function public.eventtech_web_list_pending_users() to authenticated;
grant execute on function public.eventtech_web_set_user_approval(uuid, boolean) to authenticated;

drop policy if exists "Authenticated users can read web attachments"
    on public.eventtech_web_attachments;
create policy "Approved users can read web attachments"
    on public.eventtech_web_attachments
    for select
    to authenticated
    using (public.eventtech_web_is_approved());

drop policy if exists "Authenticated users can add web attachments"
    on public.eventtech_web_attachments;
create policy "Approved users can add web attachments"
    on public.eventtech_web_attachments
    for insert
    to authenticated
    with check (
        public.eventtech_web_is_approved()
        and exists (
            select 1
            from public.sync_records
            where id = article_id
              and entity_type = 'articles'
              and deleted = false
        )
    );

drop policy if exists "Authenticated users can delete web attachments"
    on public.eventtech_web_attachments;
create policy "Approved users can delete web attachments"
    on public.eventtech_web_attachments
    for delete
    to authenticated
    using (public.eventtech_web_is_approved());

drop policy if exists "Authenticated users can read eventtech web attachments"
    on storage.objects;
create policy "Approved users can read eventtech web attachments"
    on storage.objects
    for select
    to authenticated
    using (
        bucket_id = 'eventtech-web-attachments'
        and public.eventtech_web_is_approved()
    );

drop policy if exists "Authenticated users can upload eventtech web attachments"
    on storage.objects;
create policy "Approved users can upload eventtech web attachments"
    on storage.objects
    for insert
    to authenticated
    with check (
        bucket_id = 'eventtech-web-attachments'
        and public.eventtech_web_is_approved()
    );

drop policy if exists "Authenticated users can update eventtech web attachments"
    on storage.objects;
create policy "Approved users can update eventtech web attachments"
    on storage.objects
    for update
    to authenticated
    using (
        bucket_id = 'eventtech-web-attachments'
        and public.eventtech_web_is_approved()
    )
    with check (
        bucket_id = 'eventtech-web-attachments'
        and public.eventtech_web_is_approved()
    );

drop policy if exists "Authenticated users can delete eventtech web attachments"
    on storage.objects;
create policy "Approved users can delete eventtech web attachments"
    on storage.objects
    for delete
    to authenticated
    using (
        bucket_id = 'eventtech-web-attachments'
        and public.eventtech_web_is_approved()
    );
