import datetime as dt
from pathlib import Path

import sepa_xml_check as s

HERE = Path(__file__).parent
TODAY = dt.date(2026, 11, 3)


def messages(report, severity="error"):
    return "\n".join(f.message for f in report.findings if f.severity == severity)


def test_iban():
    assert s.iban_problem("NL91ABNA0417164300") is None
    assert s.iban_problem("DE89370400440532013000") is None
    assert s.iban_problem("FR1420041010050500013M02606") is None
    assert "check digits" in s.iban_problem("NL91ABNA0417164301")
    assert "NL IBANs have 18" in s.iban_problem("NL91ABNA041716430")
    assert "spaces" in s.iban_problem("NL91 ABNA 0417 1643 00")


def test_creditor_id():
    assert s.creditor_id_problem("DE98ZZZ09999999999") is None
    assert s.creditor_id_problem("AT61ZZZ01234567890") is None
    assert "check digits" in s.creditor_id_problem("DE98ZZZ09999999998")
    assert s.creditor_id_problem("123") is not None


def test_bic():
    assert s.bic_problem("COBADEFFXXX") is None
    assert s.bic_problem("ABNANL2A") is None
    assert s.bic_problem("abnanl2a") is not None
    assert s.bic_problem("ABNANL") is not None


def test_identifier_rules():
    assert s.identifier_problems("INV-2026-0001") == []
    assert any("//" in p for p in s.identifier_problems("INV//1"))
    assert any("maximum 35" in p for p in s.identifier_problems("X" * 36))
    assert any("not allowed" in p for p in s.identifier_problems("Rechnung_1"))


def test_valid_example_has_no_findings():
    report = s.check((HERE / "example_pain008.xml").read_bytes(), "example", today=TODAY)
    assert report.findings == []
    assert report.message_type == "SEPA direct debit"
    assert report.version == "pain.008.001.02"
    assert report.transactions == 2
    assert report.total == "149.50"


def test_example_with_errors():
    report = s.check((HERE / "example_pain008_errors.xml").read_bytes(), "errors", today=TODAY)
    errors = messages(report)
    assert "NbOfTxs says 3, but there are 2 transactions" in errors
    assert "CtrlSum says 150.00, but the transactions add up to 149.50" in errors
    assert "sequence type 'RECUR' is not valid" in errors
    assert "collection date (ReqdColltnDt) 2026-10-01 is in the past" in errors
    assert "creditor identifier DE98ZZZ09999999998 has wrong check digits" in errors
    assert "debtor IBAN NL91ABNA0417164301 has wrong check digits" in errors
    assert "EndToEndId must not start or end with '/' or contain '//'" in errors
    assert "mandate was signed on 2026-10-05, after the collection date" in errors
    assert "debtor name contains characters outside the basic SEPA character set" in messages(report, "warning")


def test_not_xml():
    report = s.check(b"this is not xml", "broken")
    assert "not well-formed XML" in messages(report)


def test_other_xml():
    report = s.check(b"<Invoice/>", "other")
    assert "not a SEPA pain.008" in messages(report)


PAIN001 = """<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pain.001.001.09">
  <CstmrCdtTrfInitn>
    <GrpHdr>
      <MsgId>TRF-2026-11-001</MsgId>
      <CreDtTm>2026-11-03T10:00:00</CreDtTm>
      <NbOfTxs>1</NbOfTxs>
      <CtrlSum>{amount}</CtrlSum>
      <InitgPty><Nm>Example B.V.</Nm></InitgPty>
    </GrpHdr>
    <PmtInf>
      <PmtInfId>TRF-2026-11</PmtInfId>
      <PmtMtd>TRF</PmtMtd>
      <ReqdExctnDt><Dt>{date}</Dt></ReqdExctnDt>
      <Dbtr><Nm>Example B.V.</Nm></Dbtr>
      <DbtrAcct><Id><IBAN>NL91ABNA0417164300</IBAN></Id></DbtrAcct>
      <DbtrAgt><FinInstnId><BICFI>ABNANL2A</BICFI></FinInstnId></DbtrAgt>
      <CdtTrfTxInf>
        <PmtId><EndToEndId>INV-2026-0042</EndToEndId></PmtId>
        <Amt><InstdAmt Ccy="{currency}">{amount}</InstdAmt></Amt>
        <CdtrAgt><FinInstnId><BICFI>{bic}</BICFI></FinInstnId></CdtrAgt>
        <Cdtr><Nm>Supplier GmbH</Nm></Cdtr>
        <CdtrAcct><Id><IBAN>DE89370400440532013000</IBAN></Id></CdtrAcct>
        <RmtInf><Ustrd>Invoice 2026-0042</Ustrd></RmtInf>
      </CdtTrfTxInf>
    </PmtInf>
  </CstmrCdtTrfInitn>
</Document>"""


def pain001(**values):
    base = {"amount": "1250.00", "date": "2026-11-05", "currency": "EUR", "bic": "COBADEFFXXX"}
    base.update(values)
    return PAIN001.format(**base).encode()


def test_valid_credit_transfer():
    report = s.check(pain001(), "trf", today=TODAY)
    assert report.findings == []
    assert report.message_type == "SEPA credit transfer"
    assert report.total == "1250.00"


def test_credit_transfer_errors():
    report = s.check(pain001(amount="12.345", currency="USD", bic="COBADE", date="2026-11-01"),
                     "trf", today=TODAY)
    errors = messages(report)
    assert "has more than two decimals" in errors
    assert "currency is USD" in errors
    assert "creditor BIC COBADE is not a valid BIC" in errors
    assert "execution date (ReqdExctnDt) 2026-11-01 is in the past" in messages(report, "warning")


def test_legacy_version_warning():
    data = (HERE / "example_pain008.xml").read_bytes().replace(b"pain.008.001.02", b"pain.008.003.02")
    report = s.check(data, "legacy", today=TODAY)
    assert "old German (DK) version" in messages(report, "warning")


def test_cli_exit_codes(capsys):
    assert s.main([str(HERE / "example_pain008.xml"), "--today", "2026-11-03"]) == 0
    assert s.main([str(HERE / "example_pain008_errors.xml"), "--today", "2026-11-03"]) == 1
    out = capsys.readouterr().out
    assert "No problems found." in out
    assert "ERROR" in out
