# sepa-xml-check

Lightweight checks for SEPA XML payment files: direct debits (pain.008) and credit transfers (pain.001). It finds the mistakes that most often make a bank reject a file, and tells you which transaction they are in.

One Python file, no dependencies.

## What it checks

- Number of transactions (`NbOfTxs`) and control sums (`CtrlSum`), in the group header and per payment block
- IBANs: length per country and check digits
- BIC format
- Creditor identifier (Gläubiger-ID, incassant-ID): format and check digits
- Collection date (direct debits) and execution date (credit transfers) in the past
- Mandate: missing mandate ID, signature date in the future or after the collection date
- Sequence type (`FRST`, `RCUR`, `OOFF`, `FNAL`) and local instrument (`CORE`, `B2B`)
- Amounts: greater than zero, at most two decimals, in EUR, below the SEPA maximum
- References (`MsgId`, `PmtInfId`, `EndToEndId`, `MndtId`): length, allowed characters, no leading, trailing or double slashes
- Names and remittance information: length, and characters outside the basic SEPA character set
- Old German versions such as `pain.008.003.02`

## Install

Python 3.8 or newer.

```
pip install git+https://github.com/Bedrijfsboekhouder/sepa-xml-check
```

Or download `sepa_xml_check.py` and run it directly.

## Usage

```
sepa-xml-check my-direct-debit.xml
```

Output for `example_pain008_errors.xml` in this repository:

```
example_pain008_errors.xml
  SEPA direct debit, pain.008.001.02: 2 transactions, EUR 149.50
  ERROR   Group header: NbOfTxs says 3, but there are 2 transactions
  ERROR   Group header: CtrlSum says 150.00, but the transactions add up to 149.50
  ERROR   Payment 1 (PMT-2026-10): sequence type 'RECUR' is not valid (use FRST, RCUR, OOFF or FNAL)
  ERROR   Payment 1 (PMT-2026-10): collection date (ReqdColltnDt) 2026-10-01 is in the past
  ERROR   Payment 1 (PMT-2026-10): creditor identifier DE98ZZZ09999999998 has wrong check digits (probably a typo)
  ERROR   Payment 1 (PMT-2026-10), transaction 1 (MEMBER-0001-2026-10): debtor IBAN NL91ABNA0417164301 has wrong check digits (probably a typo)
  ERROR   Payment 1 (PMT-2026-10), transaction 2 (MEMBER//0002): EndToEndId must not start or end with '/' or contain '//'
  WARNING Payment 1 (PMT-2026-10), transaction 2 (MEMBER//0002): debtor name contains characters outside the basic SEPA character set (& ö ü); some banks reject or replace them
  ERROR   Payment 1 (PMT-2026-10), transaction 2 (MEMBER//0002): mandate was signed on 2026-10-05, after the collection date
  8 error(s), 1 warning(s)
```

Options:

- `--json` prints the result as JSON
- `--today 2026-11-03` compares dates against another day than today

The exit code is 1 when the file has errors, so you can use it in scripts and CI.

From Python:

```python
from sepa_xml_check import check

with open("direct-debit.xml", "rb") as f:
    report = check(f.read(), "direct-debit.xml")

for finding in report.errors:
    print(finding.where, finding.message)
```

## What it does not check

This is not a validation against the ISO 20022 XSD schema, and it does not check postal addresses or the extra rules of individual banks.

For that, use [SEPA File Checker](https://sepafilechecker.com). It validates against the schema and the rules banks apply, shows every error with the transaction it belongs to, and can repair the file automatically. The check is free and runs in your browser.

## Deutsch

`sepa-xml-check` prüft SEPA-Lastschriftdateien (pain.008) und SEPA-Überweisungsdateien (pain.001) auf die häufigsten Fehler, wegen derer die Bank eine Datei ablehnt: IBAN, BIC, Gläubiger-ID, Kontrollsummen, Fälligkeitsdatum, Mandatsdaten, Sequenztyp und Zeichensatz. Eine vollständige Prüfung mit Schemavalidierung gibt es kostenlos auf [sepafilechecker.com/de](https://sepafilechecker.com/de/).

## Nederlands

`sepa-xml-check` controleert SEPA-incassobestanden (pain.008) en betaalbestanden (pain.001) op de fouten waardoor een bank een bestand afkeurt: IBAN, BIC, incassant-ID, controletotalen, incassodatum, machtigingsgegevens, sequentietype en tekenset. Een volledige controle doe je gratis op [bedrijfsboekhouder.com](https://bedrijfsboekhouder.com/sepa-incassobestand-controleren/).

## License

MIT. Made by [Bedrijfsboekhouder.com](https://bedrijfsboekhouder.com), the team behind [SEPA File Checker](https://sepafilechecker.com).
