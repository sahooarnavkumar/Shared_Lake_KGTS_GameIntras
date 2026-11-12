# The Shared Lake: Flask version

Needs Python 3.9+ and Flask.

    pip install flask
    python app.py

Folder layout (keep it exactly like this):

    shared-lake-flask/
      app.py
      game.py
      test_game.py
      requirements.txt
      public/
        index.html

1. On the host computer open  http://localhost:3000/host  (game master + big-screen view).
2. Players on the same Wi-Fi open the "Players join at" URL printed in the terminal.
   Each team joins one village; the first member is captain (anyone can take the role).
3. Host sets round range / timers, optionally fills empty villages with bots, presses Start.

Windows: when the firewall asks about Python, tick "Private networks" and click Allow.

Tests: python test_game.py        Change port: set PORT=4000 (PowerShell: $env:PORT=4000)
