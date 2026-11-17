#!/bin/sh
# by warp3r(2026)
###
# czyszczenie Python Cache
sudo rm -rf `find . -name "__pycache__"`
#
# poprawienie uprawnien do plikow i katalogow
sudo chmod 775 $(find . -type d)
sudo chmod 664 $(find . -type f)
#
# aktualizacja repozytorium
git add .
git commit -m "- $1"
git push -u origin master
#
#EOF
