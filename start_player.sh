#!/bin/bash

git clone https://github.com/ucl-cs-diamant/diamant_game_interface
python3 -m venv venv
source venv/bin/activate

if test -f "requirements.txt"; then
  python3 -m pip install -r requirements.txt
fi
python3 main.py
