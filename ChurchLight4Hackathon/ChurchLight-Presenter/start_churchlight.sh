#!/bin/sh
# Starts ChurchLight Presenter and opens the operator window.
cd "$(dirname "$0")" || exit 1
exec python3 -m churchlight_presenter --db churchlight.db --media media --open --no-console
