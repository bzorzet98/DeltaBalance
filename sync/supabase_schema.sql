-- =============================================================
-- DeltaBalance — sync/supabase_schema.sql
--
-- Lo que la sincronización necesita en Supabase (sync/sync_engine.py,
-- docs/DATA_MODEL_DECISIONS.md sección 24). Se corre UNA vez en el SQL
-- Editor del proyecto de Supabase (Dashboard → SQL Editor → pegar → Run).
-- Es idempotente: correrlo de nuevo no rompe nada.
--
-- deltabalance_filas: una fila por fila local, de cualquier tabla
--   sincronizada. Clave (usuario_id, tabla, clave). La fila entera va en
--   `datos` (jsonb): así una columna nueva en la base local no obliga a
--   tocar esto.
-- deltabalance_hogar_miembros: quién es miembro de cada hogar, por el
--   codigo_invitacion del hogar (lo carga la app al subir cada hogar).
--
-- RLS: cada usuario lee, escribe y borra SOLO sus filas; además LEE las
-- filas compartidas (hogar_codigo) de los hogares de los que es miembro.
-- La service_role key (migration/subir_a_supabase.py) saltea RLS.
-- =============================================================

create table if not exists public.deltabalance_filas (
    usuario_id        uuid        not null default auth.uid() references auth.users (id) on delete cascade,
    tabla             text        not null,
    clave             text        not null,
    datos             jsonb       not null default '{}'::jsonb,
    hogar_codigo      text,
    borrado           boolean     not null default false,
    actualizado_local text,
    subido_en         timestamptz not null default now(),
    primary key (usuario_id, tabla, clave)
);

create index if not exists deltabalance_filas_bajada
    on public.deltabalance_filas (usuario_id, tabla, subido_en);
create index if not exists deltabalance_filas_hogar
    on public.deltabalance_filas (hogar_codigo) where hogar_codigo is not null;

create table if not exists public.deltabalance_hogar_miembros (
    codigo     text not null,
    usuario_id uuid not null default auth.uid() references auth.users (id) on delete cascade,
    unido_en   timestamptz not null default now(),
    primary key (codigo, usuario_id)
);

-- subido_en = hora del servidor en CADA escritura (también en un upsert que
-- actualiza): la app baja "lo subido después de la última vez".
create or replace function public.deltabalance_tocar_subido_en()
returns trigger
language plpgsql
as $$
begin
    new.subido_en := now();
    return new;
end;
$$;

drop trigger if exists deltabalance_filas_subido_en on public.deltabalance_filas;
create trigger deltabalance_filas_subido_en
    before insert or update on public.deltabalance_filas
    for each row execute function public.deltabalance_tocar_subido_en();

-- Los hogares del usuario actual. security definer: las políticas de abajo
-- la usan sin volver a pasar por RLS (si no, la política de
-- deltabalance_hogar_miembros se consultaría a sí misma: recursión).
create or replace function public.deltabalance_mis_hogares()
returns setof text
language sql
stable
security definer
set search_path = public
as $$
    select codigo from public.deltabalance_hogar_miembros where usuario_id = auth.uid();
$$;

alter table public.deltabalance_filas enable row level security;
alter table public.deltabalance_hogar_miembros enable row level security;

grant select, insert, update, delete on public.deltabalance_filas to authenticated;
grant select, insert, update, delete on public.deltabalance_hogar_miembros to authenticated;
grant execute on function public.deltabalance_mis_hogares() to authenticated;

-- --- deltabalance_filas ---
drop policy if exists "filas: leer propias y del hogar" on public.deltabalance_filas;
create policy "filas: leer propias y del hogar" on public.deltabalance_filas
    for select to authenticated
    using (
        usuario_id = auth.uid()
        or (hogar_codigo is not null and hogar_codigo in (select public.deltabalance_mis_hogares()))
    );

drop policy if exists "filas: crear propias" on public.deltabalance_filas;
create policy "filas: crear propias" on public.deltabalance_filas
    for insert to authenticated
    with check (usuario_id = auth.uid());

drop policy if exists "filas: editar propias" on public.deltabalance_filas;
create policy "filas: editar propias" on public.deltabalance_filas
    for update to authenticated
    using (usuario_id = auth.uid())
    with check (usuario_id = auth.uid());

drop policy if exists "filas: borrar propias" on public.deltabalance_filas;
create policy "filas: borrar propias" on public.deltabalance_filas
    for delete to authenticated
    using (usuario_id = auth.uid());

-- --- deltabalance_hogar_miembros ---
-- Unirse a un hogar = conocer su código (el mismo criterio que la
-- invitación local por codigo_invitacion).
drop policy if exists "miembros: ver los de mis hogares" on public.deltabalance_hogar_miembros;
create policy "miembros: ver los de mis hogares" on public.deltabalance_hogar_miembros
    for select to authenticated
    using (usuario_id = auth.uid() or codigo in (select public.deltabalance_mis_hogares()));

drop policy if exists "miembros: unirme" on public.deltabalance_hogar_miembros;
create policy "miembros: unirme" on public.deltabalance_hogar_miembros
    for insert to authenticated
    with check (usuario_id = auth.uid());

drop policy if exists "miembros: actualizar mi membresía" on public.deltabalance_hogar_miembros;
create policy "miembros: actualizar mi membresía" on public.deltabalance_hogar_miembros
    for update to authenticated
    using (usuario_id = auth.uid())
    with check (usuario_id = auth.uid());

drop policy if exists "miembros: salir" on public.deltabalance_hogar_miembros;
create policy "miembros: salir" on public.deltabalance_hogar_miembros
    for delete to authenticated
    using (usuario_id = auth.uid());
