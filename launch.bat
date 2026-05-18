@echo off
setlocal enableextensions enabledelayedexpansion

REM ============================================================
REM  Movie Automator - Windows Startup Script
REM ============================================================

echo.
echo  ==========================================
echo    Movie Automator - Starting Up
echo  ==========================================
echo.

REM ------------------------------------------------------------
REM  Get script directory from %CD% (avoids %~dp0 issues)
REM ------------------------------------------------------------
set "SCRIPT_DIR=%CD%"

REM ------------------------------------------------------------
REM  1. Check Python
REM ------------------------------------------------------------
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python is not installed or not in PATH.
    echo          Download it from https://www.python.org/downloads/
    echo          Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)
echo  [OK] Python found.

REM ------------------------------------------------------------
REM  2. Find PostgreSQL
REM ------------------------------------------------------------
set "PG_BIN="

psql --version >nul 2>&1
if not errorlevel 1 set "PG_BIN=ON_PATH"

if "!PG_BIN!"=="" (
    if exist "C:\Program Files\PostgreSQL\18\bin\psql.exe" set "PG_BIN=C:\Program Files\PostgreSQL\18\bin"
)
if "!PG_BIN!"=="" (
    if exist "C:\Program Files\PostgreSQL\17\bin\psql.exe" set "PG_BIN=C:\Program Files\PostgreSQL\17\bin"
)
if "!PG_BIN!"=="" (
    if exist "C:\Program Files\PostgreSQL\16\bin\psql.exe" set "PG_BIN=C:\Program Files\PostgreSQL\16\bin"
)
if "!PG_BIN!"=="" (
    if exist "C:\Program Files\PostgreSQL\15\bin\psql.exe" set "PG_BIN=C:\Program Files\PostgreSQL\15\bin"
)
if "!PG_BIN!"=="" (
    if exist "C:\Program Files\PostgreSQL\14\bin\psql.exe" set "PG_BIN=C:\Program Files\PostgreSQL\14\bin"
)
if "!PG_BIN!"=="" (
    if exist "C:\Program Files\PostgreSQL\13\bin\psql.exe" set "PG_BIN=C:\Program Files\PostgreSQL\13\bin"
)
if "!PG_BIN!"=="" (
    if exist "C:\Program Files (x86)\PostgreSQL\17\bin\psql.exe" set "PG_BIN=C:\Program Files (x86)\PostgreSQL\17\bin"
)
if "!PG_BIN!"=="" (
    if exist "C:\Program Files (x86)\PostgreSQL\16\bin\psql.exe" set "PG_BIN=C:\Program Files (x86)\PostgreSQL\16\bin"
)
if "!PG_BIN!"=="" (
    if exist "C:\Program Files (x86)\PostgreSQL\15\bin\psql.exe" set "PG_BIN=C:\Program Files (x86)\PostgreSQL\15\bin"
)
if "!PG_BIN!"=="" (
    if exist "C:\Program Files (x86)\PostgreSQL\14\bin\psql.exe" set "PG_BIN=C:\Program Files (x86)\PostgreSQL\14\bin"
)
if "!PG_BIN!"=="" (
    if exist "C:\Program Files (x86)\PostgreSQL\13\bin\psql.exe" set "PG_BIN=C:\Program Files (x86)\PostgreSQL\13\bin"
)

if "!PG_BIN!"=="" goto :pg_missing

if "!PG_BIN!"=="ON_PATH" (
    echo  [OK] PostgreSQL found on PATH.
) else (
    echo  [OK] PostgreSQL found at: !PG_BIN!
    set "PATH=!PATH!;!PG_BIN!"
)
goto :pg_ready

:pg_missing
echo.
echo  [ERROR] PostgreSQL is NOT installed.
echo.
echo  What is PostgreSQL?
echo  -------------------
echo  PostgreSQL is a free database server this app uses to store
echo  movie data, settings, and logs.  It replaces SQLite and fixes
echo  the database locked errors caused by multiple threads writing
echo  at the same time.
echo.
echo  How to install it:
echo  ------------------
echo  1. Go to https://www.postgresql.org/download/windows/
echo  2. Click "Download the installer" (EDB installer is easiest).
echo  3. Run the installer. When asked:
echo       - Password for superuser "postgres": pick any password and
echo         WRITE IT DOWN - you will need it the first time you run
echo         this script after installing.
echo       - Port: leave as 5432 (default).
echo       - Locale: leave as default.
echo  4. Finish the install, then run this script again.
echo.
pause
exit /b 1

:pg_ready

REM ------------------------------------------------------------
REM  3. Read or create db.conf via Python helper (ma_conf.py)
REM ------------------------------------------------------------
set "DB_HOST=localhost"
set "DB_PORT=5432"
set "DB_NAME=movie_automator"
set "DB_USER=movie_user"
set "DB_PASS=movie_password"
set "PG_SUPERPASS="

python "!SCRIPT_DIR!\ma_conf.py" check > "%TEMP%\ma_check.txt" 2>&1
set /p CONF_STATUS= < "%TEMP%\ma_check.txt"
del "%TEMP%\ma_check.txt" >nul 2>&1

if "!CONF_STATUS!"=="EXISTS" goto :read_conf

REM --- First time setup ---
echo.
echo  --------------------------------------------------------
echo   First-time database setup
echo  --------------------------------------------------------
echo.
echo  We need to create the app database inside PostgreSQL.
echo  Enter the password you chose for the "postgres" superuser
echo  when you installed PostgreSQL.
echo.
set /p "PG_SUPERPASS=  Postgres superuser password: "
echo.
echo  App database credentials (press Enter to accept defaults):
echo.

set /p "INPUT=    Host        [localhost]        : "
if not "!INPUT!"=="" set "DB_HOST=!INPUT!"
set "INPUT="

set /p "INPUT=    Port        [5432]             : "
if not "!INPUT!"=="" set "DB_PORT=!INPUT!"
set "INPUT="

set /p "INPUT=    DB name     [movie_automator]  : "
if not "!INPUT!"=="" set "DB_NAME=!INPUT!"
set "INPUT="

set /p "INPUT=    DB user     [movie_user]       : "
if not "!INPUT!"=="" set "DB_USER=!INPUT!"
set "INPUT="

set /p "INPUT=    DB password [movie_password]   : "
if not "!INPUT!"=="" set "DB_PASS=!INPUT!"
set "INPUT="

python "!SCRIPT_DIR!\ma_conf.py" write
echo.
echo  [OK] Config saved to db.conf (next run will skip this step).
goto :conf_done

:read_conf
echo  [OK] Found existing database config (db.conf).
python "!SCRIPT_DIR!\ma_conf.py" read > "%TEMP%\ma_env.bat" 2>&1
call "%TEMP%\ma_env.bat"
del "%TEMP%\ma_env.bat" >nul 2>&1

:conf_done

REM ------------------------------------------------------------
REM  4. Create PostgreSQL user and database if needed
REM ------------------------------------------------------------
set "PGPASSWORD=!PG_SUPERPASS!"

echo.
echo  [DB] Checking database user "!DB_USER!" ...
psql -U postgres -h !DB_HOST! -p !DB_PORT! -tc "SELECT 1 FROM pg_roles WHERE rolname='!DB_USER!'" 2>nul | find "1" >nul
if errorlevel 1 (
    echo  [DB] Creating user "!DB_USER!" ...
    psql -U postgres -h !DB_HOST! -p !DB_PORT! -c "CREATE USER !DB_USER! WITH PASSWORD '!DB_PASS!';" >nul 2>&1
    if errorlevel 1 (
        echo.
        echo  [ERROR] Could not create the database user.
        echo.
        echo  Most likely causes:
        echo    - Wrong postgres superuser password in db.conf
        echo      Delete db.conf and re-run this script to enter it again.
        echo    - PostgreSQL service is not running.
        echo      Press Win+R, type: services.msc
        echo      Find "postgresql-x64-XX" and click Start.
        echo.
        set "PGPASSWORD="
        pause
        exit /b 1
    )
    echo  [OK] User "!DB_USER!" created.
) else (
    echo  [OK] User "!DB_USER!" already exists.
)

echo  [DB] Checking database "!DB_NAME!" ...
psql -U postgres -h !DB_HOST! -p !DB_PORT! -tc "SELECT 1 FROM pg_database WHERE datname='!DB_NAME!'" 2>nul | find "1" >nul
if errorlevel 1 (
    echo  [DB] Creating database "!DB_NAME!" ...
    psql -U postgres -h !DB_HOST! -p !DB_PORT! -c "CREATE DATABASE !DB_NAME! OWNER !DB_USER!;" >nul 2>&1
    if errorlevel 1 (
        echo  [ERROR] Could not create database "!DB_NAME!".
        set "PGPASSWORD="
        pause
        exit /b 1
    )
    echo  [OK] Database "!DB_NAME!" created.
) else (
    echo  [OK] Database "!DB_NAME!" already exists.
)

psql -U postgres -h !DB_HOST! -p !DB_PORT! -c "GRANT ALL PRIVILEGES ON DATABASE !DB_NAME! TO !DB_USER!;" >nul 2>&1
echo  [OK] Privileges granted.

set "PGPASSWORD="
set "PG_SUPERPASS="

REM ------------------------------------------------------------
REM  5. Export app DB credentials as environment variables
REM ------------------------------------------------------------
set "DB_HOST=!DB_HOST!"
set "DB_PORT=!DB_PORT!"
set "DB_NAME=!DB_NAME!"
set "DB_USER=!DB_USER!"
set "DB_PASSWORD=!DB_PASS!"

REM ------------------------------------------------------------
REM  6. Python virtual environment
REM ------------------------------------------------------------
echo.
if not exist "!SCRIPT_DIR!\venv" (
    echo  [SETUP] Creating Python virtual environment...
    python -m venv "!SCRIPT_DIR!\venv"
)
call "!SCRIPT_DIR!\venv\Scripts\activate.bat"
echo  [OK] Virtual environment active.

REM ------------------------------------------------------------
REM  7. Install / update Python dependencies
REM ------------------------------------------------------------
echo  [SETUP] Installing dependencies (first run may take a minute)...
pip install -r "!SCRIPT_DIR!\requirements.txt" --quiet --disable-pip-version-check
if errorlevel 1 (
    echo  [ERROR] pip install failed. See output above for details.
    pause
    exit /b 1
)
echo  [OK] Dependencies installed.

REM ------------------------------------------------------------
REM  8. Launch the server
REM ------------------------------------------------------------
echo.
echo  ==========================================
echo    Movie Automator running on port 4040
echo    Open: http://localhost:4040
echo  ==========================================
echo.

cd /d "!SCRIPT_DIR!\backend"
python main.py

pause
