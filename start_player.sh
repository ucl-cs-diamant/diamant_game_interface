#!/bin/bash

python3 -m venv venv
source venv/bin/activate

if test -f "requirements.txt"; then
  python3 -m pip install -r requirements.txt
fi
python3 main.py
