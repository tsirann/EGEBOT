

Только условие с картинкой
```powershell
python .\reshu_screenshot.py "https://ege.sdamgia.ru/problem?id=12345" --condition-only
```


Браузер открываяется не на экране
```powershell
python .\reshu_screenshot.py "https://ege.sdamgia.ru/problem?id=12345" --condition-only --background
```

```

Свой путь для файла:

```powershell
python .\reshu_screenshot.py "https://ege.sdamgia.ru/problem?id=12345" -o ".\out\task.png"
```

Свой CSS-селектор, если автоопределение блока задания промахнулось:

```powershell
python .\reshu_screenshot.py "https://ege.sdamgia.ru/problem?id=12345" --selector ".prob_maindiv"
```

Браузер запускается в фоне

```powershell
python .\reshu_screenshot.py "https://ege.sdamgia.ru/problem?id=12345" --condition-only --headless
```

Можно вручную уменьшить ожидание:

```powershell
python .\reshu_screenshot.py "https://ege.sdamgia.ru/problem?id=12345" --condition-only --background --wait-ms 100
```