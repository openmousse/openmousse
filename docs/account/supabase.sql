-- OpenMousse accounts on Supabase (2026-09-29). Run once in the project's SQL editor (or through the Management API).
-- Sign-in is Supabase Auth with a 6-digit email code; the account only remembers which claws a person connects.
-- No tokens, chats or memory are ever stored here: those stay on each person's own claw.

create table if not exists public.claws (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid() references auth.users (id) on delete cascade,
  base text not null check (char_length(base) between 1 and 300),          -- the server address the app connects to
  name text not null default '' check (char_length(name) <= 120),          -- the assistant's name (app_name)
  claw_kind text check (char_length(claw_kind) <= 40),                      -- openclaw / hermes / nanobot / letta …
  claw_name text check (char_length(claw_name) <= 80),
  last_seen timestamptz,
  created_at timestamptz not null default now(),
  unique (user_id, base)
);

alter table public.claws enable row level security;

drop policy if exists "own claws: read" on public.claws;
drop policy if exists "own claws: add" on public.claws;
drop policy if exists "own claws: change" on public.claws;
drop policy if exists "own claws: remove" on public.claws;
create policy "own claws: read" on public.claws for select to authenticated using (user_id = (select auth.uid()));
create policy "own claws: add" on public.claws for insert to authenticated with check (user_id = (select auth.uid()));
create policy "own claws: change" on public.claws for update to authenticated
  using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));
create policy "own claws: remove" on public.claws for delete to authenticated using (user_id = (select auth.uid()));

revoke all on public.claws from anon;
grant select, insert, update, delete on public.claws to authenticated;

-- Delete my account (the App Store requires it in the app): removes the auth user; their claws go with it (on delete cascade).
create or replace function public.delete_user() returns void
  language sql security definer set search_path = ''
as $$
  delete from auth.users where id = (select auth.uid());
$$;
revoke execute on function public.delete_user() from public, anon;
grant execute on function public.delete_user() to authenticated;

-- For the project owner (SQL editor only, not exposed to the app): who uses OpenMousse and with which claws.
create or replace view public.owner_overview with (security_invoker = true) as
  select u.email, u.created_at as signed_up, u.last_sign_in_at,
         count(c.id) as claws, string_agg(distinct coalesce(c.claw_name, c.claw_kind), ', ') as claw_kinds, max(c.last_seen) as last_seen
  from auth.users u left join public.claws c on c.user_id = u.id
  group by u.id, u.email, u.created_at, u.last_sign_in_at;
revoke all on public.owner_overview from anon, authenticated;
