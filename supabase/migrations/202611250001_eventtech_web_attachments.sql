create table if not exists public.eventtech_web_attachments (
    id uuid primary key default gen_random_uuid(),
    article_id uuid not null,
    name text not null,
    mime_type text not null default 'application/octet-stream',
    size_bytes bigint not null check (size_bytes between 0 and 20971520),
    storage_path text not null unique,
    created_at timestamptz not null default now()
);

create index if not exists eventtech_web_attachments_article_id_idx
    on public.eventtech_web_attachments(article_id, created_at);

alter table public.eventtech_web_attachments enable row level security;
revoke all on public.eventtech_web_attachments from public, anon;
grant select, insert, delete on public.eventtech_web_attachments to authenticated;

drop policy if exists "Authenticated users can read web attachments"
    on public.eventtech_web_attachments;
create policy "Authenticated users can read web attachments"
    on public.eventtech_web_attachments
    for select
    to authenticated
    using (auth.uid() is not null);

drop policy if exists "Authenticated users can add web attachments"
    on public.eventtech_web_attachments;
create policy "Authenticated users can add web attachments"
    on public.eventtech_web_attachments
    for insert
    to authenticated
    with check (
        auth.uid() is not null
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
create policy "Authenticated users can delete web attachments"
    on public.eventtech_web_attachments
    for delete
    to authenticated
    using (auth.uid() is not null);

insert into storage.buckets (id, name, public, file_size_limit)
values ('eventtech-web-attachments', 'eventtech-web-attachments', false, 20971520)
on conflict (id) do update set
    public = false,
    file_size_limit = excluded.file_size_limit;

drop policy if exists "Authenticated users can read eventtech web attachments"
    on storage.objects;
create policy "Authenticated users can read eventtech web attachments"
    on storage.objects
    for select
    to authenticated
    using (
        bucket_id = 'eventtech-web-attachments'
        and auth.uid() is not null
    );

drop policy if exists "Authenticated users can upload eventtech web attachments"
    on storage.objects;
create policy "Authenticated users can upload eventtech web attachments"
    on storage.objects
    for insert
    to authenticated
    with check (
        bucket_id = 'eventtech-web-attachments'
        and auth.uid() is not null
    );

drop policy if exists "Authenticated users can update eventtech web attachments"
    on storage.objects;
create policy "Authenticated users can update eventtech web attachments"
    on storage.objects
    for update
    to authenticated
    using (
        bucket_id = 'eventtech-web-attachments'
        and auth.uid() is not null
    )
    with check (
        bucket_id = 'eventtech-web-attachments'
        and auth.uid() is not null
    );

drop policy if exists "Authenticated users can delete eventtech web attachments"
    on storage.objects;
create policy "Authenticated users can delete eventtech web attachments"
    on storage.objects
    for delete
    to authenticated
    using (
        bucket_id = 'eventtech-web-attachments'
        and auth.uid() is not null
    );
