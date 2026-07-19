@echo off
REM Wipes ALL data from the local searchify database (schema is kept).
REM Keeps alembic_version so migrations stay intact.

set PSQL="C:\Program Files\PostgreSQL\18\bin\psql.exe"
set PGPASSWORD=postgres

echo This will DELETE ALL DATA in the 'searchify' database on 127.0.0.1:5432.
set /p CONFIRM="Type YES to continue: "
if /i not "%CONFIRM%"=="YES" (
    echo Aborted.
    exit /b 1
)

%PSQL% -h 127.0.0.1 -U postgres -d searchify -v ON_ERROR_STOP=1 -c "DO $$ DECLARE t text; BEGIN FOR t IN SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version' LOOP EXECUTE format('TRUNCATE TABLE public.%%I RESTART IDENTITY CASCADE', t); END LOOP; END $$;"

if %errorlevel% neq 0 (
    echo Failed to clean database.
    exit /b 1
)

echo Done. All searchify data deleted.
