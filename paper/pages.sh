#!/usr/bin/env bash
# Build and report page count of the main text (up to \label{endofmain}) and total.
cd "$(dirname "$0")"
if command -v latexmk >/dev/null; then latexmk -pdf -interaction=nonstopmode -halt-on-error main.tex >/dev/null 2>&1
else pdflatex -interaction=nonstopmode main.tex >/dev/null 2>&1; bibtex main >/dev/null 2>&1
     pdflatex -interaction=nonstopmode main.tex >/dev/null 2>&1; pdflatex -interaction=nonstopmode main.tex >/dev/null 2>&1; fi
grep -q "^!" main.log && { echo BUILD FAILED; grep -m5 -A2 "^!" main.log; exit 1; }
main=$(grep 'newlabel{endofmain}' main.aux | grep -o '{[0-9]*}}$' | tr -dc '0-9')
total=$(pdfinfo main.pdf | awk '/Pages/{print $2}')
echo "main text ends on page $main; total pages $total"
grep -c "Overfull" main.log | xargs echo "overfull boxes:"
grep -c "undefined" main.log | xargs echo "undefined refs/cites lines:"
