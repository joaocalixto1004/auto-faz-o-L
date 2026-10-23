@echo off
rem Abre a automacao das redes sociais (Windows). Clique duas vezes neste arquivo.
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
rem Daqui em diante o arquivo é lido em UTF-8 (o chcp acima), então os acentos funcionam.
rem
rem Na primeira vez, instala sozinho o que falta: o uv (que baixa o Python 3.12), as
rem dependências e, se o Google Chrome não estiver instalado, o Chromium do Playwright.
rem Nas outras vezes só confere (é rápido) e abre a janela.
rem
rem Uso: Iniciar.bat               prepara o que faltar e abre a interface
rem      Iniciar.bat --verificar   só confere o que falta (não instala nem abre nada)
rem      Iniciar.bat --preparar    prepara o que faltar, sem abrir a interface
rem      (--janela é interno: é como este arquivo roda a janela no console minimizado)

title Automação das redes sociais
rem pushd também funciona se a pasta estiver na rede (\\servidor\pasta).
pushd "%~dp0" || goto sem_pasta
set "RAIZ=%CD%"
set "PYTHON=%RAIZ%\.venv\Scripts\python.exe"
set "LOGS=%RAIZ%\logs"
set "LOG=%RAIZ%\logs\instalacao.log"
set "LOG_INTERFACE=%RAIZ%\logs\interface.log"
set "FIM_JANELA=%RAIZ%\logs\interface.fim"
set "UV="

set "MODO=abrir"
if /i "%~1"=="--verificar" set "MODO=verificar"
if /i "%~1"=="--preparar" set "MODO=preparar"
if /i "%~1"=="--janela" set "MODO=janela"
if not "%~1"=="" if "%MODO%"=="abrir" goto opcao_desconhecida

if "%MODO%"=="verificar" goto verificar
if "%MODO%"=="janela" goto janela

call :pronto
if not errorlevel 1 goto ja_pronto
call :preparar
if errorlevel 1 goto falhou
goto preparado

:ja_pronto
if "%MODO%"=="preparar" echo ✔ Tudo pronto; nada a instalar.

:preparado
if "%MODO%"=="preparar" goto fim_ok

echo Abrindo a janela da automação...
if not exist "%LOGS%" mkdir "%LOGS%"
>>"%LOG_INTERFACE%" echo.
>>"%LOG_INTERFACE%" echo [%date% %time%] Iniciar.bat: abrindo a interface.
if exist "%FIM_JANELA%" del /q "%FIM_JANELA%" >nul 2>&1
rem Console minimizado com o python.exe (não o pythonw): o Parar da interface
rem continua parando com calma. Essa janela minimizada não deve ser fechada.
rem Ela roda este mesmo arquivo com --janela, que guarda a saída da janela em
rem logs\interface.log e, quando ela fecha, o código de saída em logs\interface.fim.
start "Automação das redes sociais - não feche esta janela" /min "%ComSpec%" /c call "%~f0" --janela
if errorlevel 1 goto falhou
rem Se a janela fechar logo depois de abrir (erro, falta de algo...), mostra o motivo em vez de sumir.
for /l %%I in (1,1,8) do (
    if exist "%FIM_JANELA%" goto janela_fechou
    ping -n 2 127.0.0.1 >nul
)
goto fim_ok

:janela_fechou
set "CODIGO_JANELA="
set /p CODIGO_JANELA=<"%FIM_JANELA%"
if "%CODIGO_JANELA%"=="0" goto fim_ok
echo.
echo ✘ A janela fechou logo depois de abrir (código %CODIGO_JANELA%). Últimas linhas de logs\interface.log:
call :mostrar_fim_do_log
>>"%LOG%" echo [%date% %time%] ERRO: a interface fechou logo depois de abrir (código %CODIGO_JANELA%).
goto falhou


rem ---------------------------------------------------------------- janela (modo interno)

:janela
rem Roda a janela guardando a saída (e os erros) em logs\interface.log, como o iniciar.sh do Linux.
title Automação das redes sociais - não feche esta janela
if not exist "%LOGS%" mkdir "%LOGS%"
"%PYTHON%" -m automacao.interface >>"%LOG_INTERFACE%" 2>&1
set "CODIGO=%errorlevel%"
>"%FIM_JANELA%" echo %CODIGO%
popd
exit /b %CODIGO%


rem ---------------------------------------------------------------- verificar

:verificar
if exist "%PYTHON%" goto verificar_python
echo ✘ Ambiente Python: a pasta .venv ainda não existe. Abra o Iniciar.bat sem opções para instalar.
popd
exit /b 1

:verificar_python
"%PYTHON%" -m automacao.instalacao --verificar
set "CODIGO=%errorlevel%"
popd
exit /b %CODIGO%


rem ---------------------------------------------------------------- subrotinas

:mostrar_fim_do_log
rem As últimas 20 linhas do log da janela (o caminho vai pela variável: nada de aspas a escapar).
set "POWERSHELL=powershell"
if exist "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" set "POWERSHELL=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
"%POWERSHELL%" -NoProfile -Command "Get-Content -LiteralPath $env:LOG_INTERFACE -Tail 20 -Encoding UTF8" 2>nul
if errorlevel 1 type "%LOG_INTERFACE%"
exit /b 0

:pronto
rem errorlevel 0 se já está tudo instalado (conferência rápida, sem instalar nada).
if not exist "%PYTHON%" exit /b 1
"%PYTHON%" -m automacao.instalacao --verificar --silencioso >nul 2>&1
exit /b %errorlevel%

:preparar
if not exist "%LOGS%" mkdir "%LOGS%"
>>"%LOG%" echo [%date% %time%] Iniciar.bat: preparando.
echo Preparando a automação. Na primeira vez pode levar alguns minutos...
call :encontrar_uv
if errorlevel 1 call :instalar_uv
if errorlevel 1 exit /b 1
>>"%LOG%" echo [%date% %time%] uv: "%UV%"
if exist "%PYTHON%" "%PYTHON%" -c "pass" >nul 2>&1 && goto preparar_dependencias
call :criar_venv
if errorlevel 1 exit /b 1

:preparar_dependencias
"%PYTHON%" -m automacao.instalacao --preparar --uv "%UV%"
if errorlevel 1 exit /b 1
exit /b 0

:encontrar_uv
set "UV="
for %%U in (uv.exe) do set "UV=%%~$PATH:U"
if defined UV exit /b 0
if defined UV_INSTALL_DIR if exist "%UV_INSTALL_DIR%\uv.exe" set "UV=%UV_INSTALL_DIR%\uv.exe"
if defined UV exit /b 0
if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
if defined UV exit /b 0
if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "UV=%USERPROFILE%\.cargo\bin\uv.exe"
if defined UV exit /b 0
exit /b 1

:instalar_uv
echo Instalando o uv, que baixa o Python e as dependências (só desta vez)...
>>"%LOG%" echo [%date% %time%] Instalando o uv com o instalador oficial.
set "POWERSHELL=powershell"
if exist "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" set "POWERSHELL=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
"%POWERSHELL%" -NoProfile -ExecutionPolicy ByPass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072; irm https://astral.sh/uv/install.ps1 | iex"
if errorlevel 1 goto instalar_uv_falhou
call :encontrar_uv
if errorlevel 1 goto instalar_uv_sumiu
exit /b 0

:instalar_uv_falhou
echo ✘ Não consegui instalar o uv. Confira a conexão com a internet e abra de novo.
>>"%LOG%" echo [%date% %time%] ERRO: o instalador do uv falhou.
exit /b 1

:instalar_uv_sumiu
echo ✘ O uv foi instalado, mas não o encontrei. Feche esta janela e abra o Iniciar.bat de novo.
>>"%LOG%" echo [%date% %time%] ERRO: uv instalado, mas não encontrado.
exit /b 1

:criar_venv
rem Python do próprio uv (--managed-python), que já traz o Tkinter. Versões antigas do
rem uv não têm essa opção; aí vale o Python que o uv escolher. O UV_VENV_CLEAR recria
rem um .venv quebrado (o uv só apaga a pasta se ela for mesmo um ambiente Python).
echo Criando o ambiente Python em .venv (o uv baixa o Python 3.12 se preciso)...
set "GERENCIADO="
"%UV%" venv --help 2>nul | findstr /c:"--managed-python" >nul && set "GERENCIADO=--managed-python"
set "UV_VENV_CLEAR=1"
"%UV%" venv --python 3.12 %GERENCIADO% "%RAIZ%\.venv"
set "CODIGO=%errorlevel%"
set "UV_VENV_CLEAR="
>>"%LOG%" echo [%date% %time%] uv venv terminou com o código %CODIGO%.
if not "%CODIGO%"=="0" echo ✘ Não consegui criar o ambiente Python (.venv): o uv terminou com o código %CODIGO%. O motivo está na mensagem do uv logo acima. Causas comuns: sem internet para baixar o Python, disco cheio, pasta sem permissão de escrita ou antivírus bloqueando.
exit /b %CODIGO%


rem ---------------------------------------------------------------- saídas

:opcao_desconhecida
echo Opção desconhecida: "%~1"
echo Use: Iniciar.bat [--verificar ^| --preparar]
popd
exit /b 2

:sem_pasta
echo ✘ Não consegui abrir a pasta do projeto: "%~dp0"
pause
exit /b 1

:falhou
echo.
echo ✘ A automação não pôde ser aberta. Leia a mensagem acima.
if exist "%LOG%" echo Detalhes em: "%LOG%"
if "%MODO%"=="abrir" pause
popd
exit /b 1

:fim_ok
popd
exit /b 0
