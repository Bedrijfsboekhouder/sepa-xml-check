#!/usr/bin/env python3
"""Lightweight checks for SEPA XML payment files (pain.008 and pain.001).

Finds the errors that most often make a bank reject a SEPA file: a wrong IBAN or
BIC, a wrong creditor identifier, control sums that do not add up, dates in the
past, invalid sequence types, characters outside the SEPA character set and
fields that are too long.

This is not a full XSD schema validation. For a complete check, including
schema validation and automatic repair, see https://sepafilechecker.com
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from typing import List, Optional

__version__ = "0.1.0"

# IBAN length per country (SEPA countries and territories).
IBAN_LENGTHS = {
    "AD": 24, "AL": 28, "AT": 20, "BE": 16, "BG": 22, "CH": 21, "CY": 28,
    "CZ": 24, "DE": 22, "DK": 18, "EE": 20, "ES": 24, "FI": 18, "FR": 27,
    "GB": 22, "GI": 23, "GR": 27, "HR": 21, "HU": 28, "IE": 22, "IS": 26,
    "IT": 27, "LI": 21, "LT": 20, "LU": 20, "LV": 21, "MC": 27, "MD": 24,
    "ME": 22, "MK": 19, "MT": 31, "NL": 18, "NO": 15, "PL": 28, "PT": 25,
    "RO": 24, "SE": 24, "SI": 19, "SK": 24, "SM": 27, "VA": 22,
}

# BIC pattern from the ISO 20022 schema.
BIC_RE = re.compile(r"^[A-Z]{6}[A-Z2-9][A-NP-Z0-9]([A-Z0-9]{3})?$")
# Anything outside the basic Latin character set of the EPC guidelines.
NOT_SEPA_CHAR_RE = re.compile(r"[^A-Za-z0-9/\-?:().,'+ ]")

SEQUENCE_TYPES = {"FRST", "RCUR", "OOFF", "FNAL"}
LOCAL_INSTRUMENTS = {"CORE", "B2B"}
LEGACY_VERSIONS = {"pain.008.002.02", "pain.008.003.02", "pain.001.002.03", "pain.001.003.03"}
CURRENT_VERSION = {"pain.008": "pain.008.001.02", "pain.001": "pain.001.001.03"}
MAX_AMOUNT = Decimal("999999999.99")


@dataclass
class Finding:
    severity: str  # "error" or "warning"
    message: str
    where: str = ""


@dataclass
class Report:
    file: str
    message_type: str = ""
    version: str = ""
    transactions: int = 0
    total: str = "0.00"
    findings: List[Finding] = field(default_factory=list)

    @property
    def errors(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == "warning"]

    def add(self, severity: str, message: str, where: str = "") -> None:
        self.findings.append(Finding(severity, message, where))


# --- building blocks -------------------------------------------------------

def _mod97(value: str) -> int:
    return int("".join(str(int(ch, 36)) for ch in value)) % 97


def iban_problem(iban: str) -> Optional[str]:
    """Return a description of what is wrong with an IBAN, or None if it is valid."""
    if " " in iban:
        return "contains spaces (write it without spaces)"
    s = iban.upper()
    if not re.fullmatch(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}", s):
        return "is not a valid IBAN"
    expected = IBAN_LENGTHS.get(s[:2])
    if expected and len(s) != expected:
        return f"has {len(s)} characters, but {s[:2]} IBANs have {expected}"
    if _mod97(s[4:] + s[:4]) != 1:
        return "has wrong check digits (probably a typo)"
    return None


def creditor_id_problem(creditor_id: str) -> Optional[str]:
    """Check a SEPA creditor identifier (Gläubiger-ID, incassant-ID)."""
    s = creditor_id.strip().upper()
    if not re.fullmatch(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{3}[A-Z0-9]{1,28}", s):
        return "is not a valid SEPA creditor identifier"
    # The business code (positions 5-7) is not part of the check digit calculation.
    expected = 98 - _mod97(s[7:] + s[:2] + "00")
    if expected != int(s[2:4]):
        return "has wrong check digits (probably a typo)"
    return None


def bic_problem(bic: str) -> Optional[str]:
    if not BIC_RE.match(bic):
        return "is not a valid BIC"
    return None


def identifier_problems(value: str, max_len: int = 35) -> List[str]:
    """Rules for references and identifiers such as MsgId, EndToEndId and MndtId."""
    problems = []
    if len(value) > max_len:
        problems.append(f"is {len(value)} characters long (maximum {max_len})")
    bad = sorted(set(NOT_SEPA_CHAR_RE.findall(value)))
    if bad:
        problems.append("contains characters that are not allowed: " + " ".join(bad))
    if value.startswith("/") or value.endswith("/") or "//" in value:
        problems.append("must not start or end with '/' or contain '//'")
    return problems


def _text(el: Optional[ET.Element], path: str) -> str:
    if el is None:
        return ""
    found = el.find(path)
    if found is None or found.text is None:
        return ""
    return found.text.strip()


def _date(value: str) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError:
        return None


def _amount(tx: ET.Element, kind: str) -> Optional[Decimal]:
    raw = _text(tx, "InstdAmt" if kind == "pain.008" else "Amt/InstdAmt")
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


# --- checks ----------------------------------------------------------------

def _check_counts(report, el, txs, kind, where, mandatory) -> None:
    total = sum((a for a in (_amount(tx, kind) for tx in txs) if a is not None), Decimal(0))
    nb = _text(el, "NbOfTxs")
    if not nb:
        if mandatory:
            report.add("error", "number of transactions (NbOfTxs) is missing", where)
    elif not nb.isdigit() or int(nb) != len(txs):
        report.add("error", f"NbOfTxs says {nb}, but there are {len(txs)} transactions", where)
    cs = _text(el, "CtrlSum")
    if cs:
        try:
            control_sum = Decimal(cs)
        except InvalidOperation:
            report.add("error", f"CtrlSum '{cs}' is not a number", where)
        else:
            if control_sum != total:
                report.add("error", f"CtrlSum says {cs}, but the transactions add up to {total:.2f}", where)


def _check_name(report, name, label, where) -> None:
    if len(name) > 70:
        report.add("error", f"{label} is {len(name)} characters long (maximum 70)", where)
    bad = sorted(set(NOT_SEPA_CHAR_RE.findall(name)))
    if bad:
        report.add("warning", f"{label} contains characters outside the basic SEPA character set "
                   f"({' '.join(bad)}); some banks reject or replace them", where)


def _check_party(report, el, party, label, where) -> None:
    name = _text(el, f"{party}/Nm")
    if not name:
        report.add("error", f"{label} name ({party}/Nm) is missing", where)
    else:
        _check_name(report, name, f"{label} name", where)
    iban = _text(el, f"{party}Acct/Id/IBAN")
    if not iban:
        report.add("error", f"{label} IBAN is missing", where)
    else:
        problem = iban_problem(iban)
        if problem:
            report.add("error", f"{label} IBAN {iban} {problem}", where)
    bic = _text(el, f"{party}Agt/FinInstnId/BIC") or _text(el, f"{party}Agt/FinInstnId/BICFI")
    if bic and bic_problem(bic):
        report.add("error", f"{label} BIC {bic} is not a valid BIC", where)


def _check_date(report, value, label, where, today, past_severity) -> Optional[dt.date]:
    if not value:
        report.add("error", f"{label} is missing", where)
        return None
    date = _date(value)
    if date is None:
        report.add("error", f"{label} '{value}' is not a valid date", where)
    elif date < today:
        report.add(past_severity, f"{label} {value} is in the past", where)
    return date


def _check_transaction(report, tx, kind, where, collection_date, today) -> None:
    e2e = _text(tx, "PmtId/EndToEndId")
    if e2e:
        where = f"{where} ({e2e})"
        for problem in identifier_problems(e2e):
            report.add("error", f"EndToEndId {problem}", where)
    else:
        report.add("error", "EndToEndId is missing", where)

    amount_el = tx.find("InstdAmt" if kind == "pain.008" else "Amt/InstdAmt")
    raw = (amount_el.text or "").strip() if amount_el is not None else ""
    if not raw:
        report.add("error", "amount is missing", where)
    else:
        try:
            amount = Decimal(raw)
        except InvalidOperation:
            report.add("error", f"amount '{raw}' is not a number", where)
        else:
            if amount <= 0:
                report.add("error", f"amount {raw} must be greater than zero", where)
            elif amount > MAX_AMOUNT:
                report.add("error", f"amount {raw} is above the SEPA maximum of 999999999.99", where)
            if amount.as_tuple().exponent < -2:
                report.add("error", f"amount {raw} has more than two decimals", where)
            currency = amount_el.get("Ccy")
            if currency != "EUR":
                report.add("error", f"currency is {currency or 'missing'}; SEPA payments are in EUR", where)

    if kind == "pain.008":
        _check_party(report, tx, "Dbtr", "debtor", where)
        mandate = tx.find("DrctDbtTx/MndtRltdInf")
        mandate_id = _text(mandate, "MndtId")
        if not mandate_id:
            report.add("error", "mandate ID (MndtId) is missing", where)
        else:
            for problem in identifier_problems(mandate_id):
                report.add("error", f"mandate ID {problem}", where)
        signed = _text(mandate, "DtOfSgntr")
        if not signed:
            report.add("error", "mandate signature date (DtOfSgntr) is missing", where)
        else:
            date = _date(signed)
            if date is None:
                report.add("error", f"mandate signature date '{signed}' is not a valid date", where)
            elif date > today:
                report.add("error", f"mandate signature date {signed} is in the future", where)
            elif collection_date and date > collection_date:
                report.add("error", f"mandate was signed on {signed}, after the collection date", where)
        tx_creditor_id = _text(tx, "DrctDbtTx/CdtrSchmeId/Id/PrvtId/Othr/Id")
        if tx_creditor_id:
            problem = creditor_id_problem(tx_creditor_id)
            if problem:
                report.add("error", f"creditor identifier {tx_creditor_id} {problem}", where)
    else:
        _check_party(report, tx, "Cdtr", "creditor", where)

    lines = [(u.text or "").strip() for u in tx.findall("RmtInf/Ustrd")]
    if len(lines) > 1:
        report.add("error", "more than one remittance line (Ustrd); SEPA allows one", where)
    for line in lines:
        if len(line) > 140:
            report.add("error", f"remittance information is {len(line)} characters long (maximum 140)", where)
        bad = sorted(set(NOT_SEPA_CHAR_RE.findall(line)))
        if bad:
            report.add("warning", "remittance information contains characters outside the basic SEPA "
                       f"character set ({' '.join(bad)}); some banks reject or replace them", where)
    if lines and tx.find("RmtInf/Strd") is not None:
        report.add("warning", "both structured and unstructured remittance information; "
                   "most banks accept only one", where)


def _check_payment(report, pmt, index, kind, today) -> None:
    pmt_id = _text(pmt, "PmtInfId")
    where = f"Payment {index}" + (f" ({pmt_id})" if pmt_id else "")
    if not pmt_id:
        report.add("error", "payment information ID (PmtInfId) is missing", where)
    else:
        for problem in identifier_problems(pmt_id):
            report.add("error", f"PmtInfId {problem}", where)

    tx_tag = "DrctDbtTxInf" if kind == "pain.008" else "CdtTrfTxInf"
    txs = pmt.findall(tx_tag)
    if not txs:
        report.add("error", "this payment block contains no transactions", where)
    _check_counts(report, pmt, txs, kind, where, mandatory=False)

    collection_date = None
    if kind == "pain.008":
        seq = _text(pmt, "PmtTpInf/SeqTp")
        if not seq:
            report.add("error", "sequence type (SeqTp) is missing", where)
        elif seq not in SEQUENCE_TYPES:
            report.add("error", f"sequence type '{seq}' is not valid (use FRST, RCUR, OOFF or FNAL)", where)
        instrument = _text(pmt, "PmtTpInf/LclInstrm/Cd")
        if instrument == "COR1":
            report.add("warning", "COR1 no longer exists since November 2017; use CORE", where)
        elif instrument and instrument not in LOCAL_INSTRUMENTS:
            report.add("error", f"local instrument '{instrument}' is not valid (use CORE or B2B)", where)
        collection_date = _check_date(report, _text(pmt, "ReqdColltnDt"),
                                      "collection date (ReqdColltnDt)", where, today, "error")
        _check_party(report, pmt, "Cdtr", "creditor", where)
        creditor_id = _text(pmt, "CdtrSchmeId/Id/PrvtId/Othr/Id")
        if creditor_id:
            problem = creditor_id_problem(creditor_id)
            if problem:
                report.add("error", f"creditor identifier {creditor_id} {problem}", where)
        elif any(not _text(tx, "DrctDbtTx/CdtrSchmeId/Id/PrvtId/Othr/Id") for tx in txs):
            report.add("error", "creditor identifier (CdtrSchmeId) is missing", where)
    else:
        execution = (_text(pmt, "ReqdExctnDt/Dt") or _text(pmt, "ReqdExctnDt/DtTm")
                     or _text(pmt, "ReqdExctnDt"))
        _check_date(report, execution, "execution date (ReqdExctnDt)", where, today, "warning")
        _check_party(report, pmt, "Dbtr", "debtor", where)

    for number, tx in enumerate(txs, 1):
        _check_transaction(report, tx, kind, f"{where}, transaction {number}", collection_date, today)


def check(data: bytes, filename: str = "", today: Optional[dt.date] = None) -> Report:
    """Check the contents of a SEPA XML file and return a Report."""
    today = today or dt.date.today()
    report = Report(file=filename)
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        report.add("error", f"the file is not well-formed XML ({exc})")
        return report

    namespace = root.tag[1:].split("}")[0] if root.tag.startswith("{") else ""
    for el in root.iter():
        if isinstance(el.tag, str):
            el.tag = el.tag.rsplit("}", 1)[-1]
    report.version = namespace.rsplit(":", 1)[-1] if namespace else ""

    body, kind = root.find("CstmrDrctDbtInitn"), "pain.008"
    if body is None:
        body, kind = root.find("CstmrCdtTrfInitn"), "pain.001"
    if root.tag != "Document" or body is None:
        report.add("error", "this is not a SEPA pain.008 (direct debit) or pain.001 (credit transfer) file")
        return report
    report.message_type = "SEPA direct debit" if kind == "pain.008" else "SEPA credit transfer"
    if report.version in LEGACY_VERSIONS:
        report.add("warning", f"{report.version} is an old German (DK) version that many banks no longer "
                   f"accept; use {CURRENT_VERSION[kind]} or newer")

    payments = body.findall("PmtInf")
    tx_tag = "DrctDbtTxInf" if kind == "pain.008" else "CdtTrfTxInf"
    all_txs = [tx for pmt in payments for tx in pmt.findall(tx_tag)]
    total = sum((a for a in (_amount(tx, kind) for tx in all_txs) if a is not None), Decimal(0))
    report.transactions = len(all_txs)
    report.total = f"{total:.2f}"

    header = body.find("GrpHdr")
    if header is None:
        report.add("error", "the group header (GrpHdr) is missing")
    else:
        msg_id = _text(header, "MsgId")
        if not msg_id:
            report.add("error", "message ID (MsgId) is missing", "Group header")
        else:
            for problem in identifier_problems(msg_id):
                report.add("error", f"MsgId {problem}", "Group header")
        _check_counts(report, header, all_txs, kind, "Group header", mandatory=True)

    if not payments:
        report.add("error", "the file contains no payment information (PmtInf)")
    for index, pmt in enumerate(payments, 1):
        _check_payment(report, pmt, index, kind, today)
    return report


# --- command line ----------------------------------------------------------

def format_report(report: Report) -> str:
    lines = [report.file]
    if report.message_type:
        lines.append(f"  {report.message_type}, {report.version or 'unknown version'}: "
                     f"{report.transactions} transactions, EUR {report.total}")
    for finding in report.findings:
        label = "ERROR  " if finding.severity == "error" else "WARNING"
        where = f"{finding.where}: " if finding.where else ""
        lines.append(f"  {label} {where}{finding.message}")
    if not report.findings:
        lines.append("  No problems found.")
    lines.append(f"  {len(report.errors)} error(s), {len(report.warnings)} warning(s)")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sepa-xml-check",
        description="Check SEPA XML files (pain.008 direct debits, pain.001 credit transfers).",
        epilog="For schema validation and automatic repair, see https://sepafilechecker.com",
    )
    parser.add_argument("files", nargs="+", help="SEPA XML file(s) to check")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    parser.add_argument("--today", type=dt.date.fromisoformat, default=None,
                        help="date to compare against (YYYY-MM-DD), default: today")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    reports = []
    for name in args.files:
        try:
            with open(name, "rb") as handle:
                data = handle.read()
        except OSError as exc:
            parser.error(str(exc))
        reports.append(check(data, name, args.today))

    if args.json:
        print(json.dumps([asdict(r) for r in reports], indent=2, ensure_ascii=False))
    else:
        print("\n\n".join(format_report(r) for r in reports))
    return 1 if any(r.errors for r in reports) else 0


if __name__ == "__main__":
    sys.exit(main())
