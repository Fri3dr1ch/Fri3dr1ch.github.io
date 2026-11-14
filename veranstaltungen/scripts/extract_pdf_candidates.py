#!/usr/bin/env python3
"""Extract a new PDF for human review. Never writes directly to the live calendar.

Usage: python scripts/extract_pdf_candidates.py INPUT.pdf OUTPUT.json
Requires pdfplumber. Review table continuations and brochure layout against PDF.
"""
import hashlib,json,sys
from pathlib import Path
import pdfplumber

path=Path(sys.argv[1]);output=Path(sys.argv[2])
with pdfplumber.open(path) as pdf:
    pages=[{'page':n+1,'text':p.extract_text(layout=True),'tables':p.extract_tables()} for n,p in enumerate(pdf.pages)]
output.write_text(json.dumps({'filename':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'reviewed':False,'pages':pages},ensure_ascii=False,indent=2)+'\n')
print('Prüfextrakt erstellt:',output)
