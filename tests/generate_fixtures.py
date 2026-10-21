"""Explicitly generate simulated inputs only inside tests/data. Not called by app.py or tests."""

import csv
import io
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "data"


def write(case, side, headers, rows, encoding="utf-8-sig", delimiter=","):
    directory = ROOT / case
    directory.mkdir(parents=True, exist_ok=True)
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=delimiter)
    writer.writerow(headers)
    writer.writerows(rows)
    (directory / f"{side}.csv").write_bytes(buffer.getvalue().encode(encoding))


def main():
    contacts_a = [["001", "林青", "13800000001", "0"], ["002", "周宁", "13800000002", "5"]]
    contacts_b = [["13800000002", "周宁", "002", "乙"], ["13800000001", "林青", "001", "甲"]]
    write("01_synonyms", "a", ["客户号", "联系人", "联系电话", "次数"], contacts_a)
    write("01_synonyms", "b", ["手机号", "联络人", "编号", "备注"], contacts_b)
    write("02_different_meanings", "a", ["单据号", "总成绩"], [["001", "80"]])
    write("02_different_meanings", "b", ["业务编号", "期末成绩"], [["001", "80"]])
    write("03_exact_duplicates", "a", ["客户号", "联系人", "联系电话", "次数"], [contacts_a[0], contacts_a[0], contacts_a[1]])
    write("03_exact_duplicates", "b", ["手机号", "联络人", "编号", "备注"], contacts_b)
    write("04_business_records", "a", ["客户号", "服务轮次", "值"], [["001", "初次", "80"], ["001", "再次", "90"]])
    write("04_business_records", "b", ["对象编号", "轮次", "标签"], [["001", "初次", "一"], ["001", "再次", "二"]])
    write("05_same_name", "a", ["姓名", "电话"], [["张伟", "13800000001"]])
    write("05_same_name", "b", ["姓名", "电话"], [["张伟", "13800000002"]])
    write("06_no_school_id", "a", ["外部编号", "邮箱"], [["AC-001", "lin@example.test"]])
    write("06_no_school_id", "b", ["账户编码", "Email"], [["AC-001", "lin@example.test"]])
    write("07_conflicts", "a", ["编号", "城市"], [["001", "上海"], ["002", "杭州"]])
    write("07_conflicts", "b", ["编号", "城市"], [["001", "北京"], ["002", "杭州"]])
    write("07_missing_keys", "a", ["客户号", "电话"], [["001", ""]])
    write("07_missing_keys", "b", ["账户编号", "电话"], [["001", ""]])
    write("08_bad_rows", "b", ["业务键", "备注"], [["001", "正常"]])
    (ROOT / "08_bad_rows/a.csv").write_text('业务键,值\n001,甲\nBROKEN,多,一列\n002,乙\n', encoding="utf-8")
    write("09_formats", "gb18030", ["编码", "备注"], [["001", "中文"], ["002", "0"]], "gb18030", ";")
    write("09_formats", "multiline", ["来源记录", "文本"], [["001", "第一行\r\n第二行"], ["002", "0"]])
    write("09_formats", "spaced", ["编号", "文本"], [[" 001 ", " Zero "]])
    print(f"Simulated fixtures: {ROOT}")


if __name__ == "__main__":
    main()
