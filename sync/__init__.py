"""
DeltaBalance — sync/

Sincronización con Supabase (módulo de aplicación, CLAUDE.md §2: depende
del motor de datos, nunca al revés):
    supabase_client.py  cliente Supabase (uno solo por proceso)
    auth.py             login / sesión guardada / nombre de display
    sync_engine.py      subir y bajar filas (docs/DATA_MODEL_DECISIONS.md sección 24)
    referencias.py      FK de filas compartidas a filas privadas de otro miembro (sección 27)
    supabase_schema.sql lo que hay que crear una vez en el SQL Editor de Supabase
"""
