@echo off
chcp 65001 >nul
cd /d %~dp0

echo ================================
echo 朝のニュースまとめを更新します
echo ================================
echo.

if not exist feeds.json (
  if exist feeds_sample.json (
    copy feeds_sample.json feeds.json >nul
    echo feeds.json がなかったため、feeds_sample.json から作成しました。
  ) else (
    echo feeds.json も feeds_sample.json も見つかりません。
    echo このbatファイルを generate_morning_news.py と同じフォルダに置いてください。
    pause
    exit /b 1
  )
)

where py >nul 2>nul
if %errorlevel%==0 (
  py generate_morning_news.py
) else (
  where python >nul 2>nul
  if %errorlevel%==0 (
    python generate_morning_news.py
  ) else (
    echo Pythonが見つかりません。Pythonをインストールしてください。
    pause
    exit /b 1
  )
)

if exist output\morning_news.html (
  start "" output\morning_news.html
) else (
  echo output\morning_news.html が見つかりませんでした。
)

echo.
echo 完了しました。エラー確認用にこの画面を残します。
pause
