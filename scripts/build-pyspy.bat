@echo off
ECHO Y | RMDIR /S py-spy

git clone --depth 1 -b v0.3.10g1 https://github.com/Granulate/py-spy.git && git -C py-spy reset --hard 480deec8b5dde3cd331d1a793106981c1796d172

IF NOT EXIST .\dep\vs_BuildTools.exe (
	ECHO Downloading Visual Studio Build Tools...
	wget -O .\dep\vs_BuildTools.exe https://aka.ms/vs/17/release/vs_buildtools.exe
)

ECHO Installing Windows build tools components...
CALL .\dep\vs_BuildTools.exe --add Microsoft.VisualStudio.Workload.VCTools --add Microsoft.VisualStudio.Component.Windows10SDK.19041 --add Microsoft.VisualStudio.Component.VC.Tools.x86.x64 --wait --passive

SET LINK_LOC="C:\Program Files (x86)\Microsoft Visual Studio\*link.exe"
SET SDK_LOC="C:\Program Files (x86)\*advapi32.lib"

DIR %LINK_LOC% /S
IF ERRORLEVEL 1 (
	ECHO Unable to find link.exe. Exiting...
        EXIT /B 1
)

DIR %SDK_LOC% /S
IF ERRORLEVEL 1 (
	ECHO Unable to find advapi32.lib. Exiting...
	EXIT /B 1 
)

ECHO "Done installing Windows Build Tools."

WHERE cargo
IF ERRORLEVEL 1 (
	ECHO cargo wasn't found. Attempting to install...
	wget -O .\dep\rustup-init.exe https://static.rust-lang.org/rustup/dist/x86_64-pc-windows-msvc/rustup-init.exe
	.\dep\rustup-init.exe -y
)

ECHO "Current Path: %PATH%"
SET PATH=%PATH%;%USERPROFILE%\.cargo\bin;%CD%\deps
ECHO "Modified Path: %PATH%"
CD py-spy
rustup default stable
rustup target add x86_64-pc-windows-gnu

WHERE cargo
IF ERRORLEVEL 1 (
       ECHO py-spy build failed.
       EXIT /B 1
)
cargo install cross
cargo build --release
DIR target\release\py-spy.exe
IF ERRORLEVEL 1 (
	ECHO py-spy build failed.
	CD ..
	EXIT /B 1
)
ECHO py-spy install complete
CD ..
COPY py-spy\target\release\py-spy.exe .\py-spy
ECHO "Current Working Directory: %CD%"
ECHO Y | RMDIR /S py-spy\target
EXIT /B 0
