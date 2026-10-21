"""Generate fictional platform CSVs for manual trials; never imported by the app."""
import copy
import csv
import io
import json
import random
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline.service import PipelineStore

TASK = ("以教务花名册为主表整合四个平台，编号按文本保留前导零。"
        "实验按最高分归并，作业和测验按提交时间取最后一次，各项目按表头满分折算百分制。"
        "仅在姓名与班级精确一致且候选唯一时补全缺失编号；不确定的记录暂停确认。"
        "实验40%，作业40%，测验20%，未交记0，缺考记0，缓考保留，免修保留，保留一位小数。")


def save_csv(path, rows):
    stream = io.StringIO(newline="")
    csv.writer(stream).writerows(rows)
    path.write_bytes(stream.getvalue().encode("utf-8-sig"))


def main():
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    package = ROOT / "outputs" / ("多平台试用数据_" + stamp)
    package.mkdir(parents=True, exist_ok=False)
    rng = random.Random(20261021)
    surnames = list("赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨许何吕施张孔曹严")
    names = ["张伟", "张伟"] + [surnames[i % len(surnames)] + ["思远", "雨桐", "子涵", "明轩", "若宁"][i % 5] for i in range(38)]
    students = [{"id": f"002026{i+1:04d}", "name": name, "class": "软件工程2601班" if i % 2 == 0 else "软件工程2602班"}
                for i, name in enumerate(names)]
    roster = [["教务系统：2026秋 数据实践课程 / 主花名册（全部为虚构测试人员）"],
              ["导出时间：2026-10-21 09:00:00；编号请按文本读取；联系电话为模拟标识"],
              ["学号", "学生姓名", "班级", "手机号", "专业"],
              *[[s["id"], s["name"], s["class"], f"SIM-PHONE-{i+1:04d}", "软件工程"] for i, s in enumerate(students)],
              ["总人数", str(len(students)), "", "", ""]]
    labs = [["实验平台 / 实验项目累计表现 / 原始分值80分（虚构测试数据）"],
            ["StudentID", "姓名", "班级", "实验成绩(满分80)", "提交时间", "提交编号", "备注"]]
    homework = [["学习平台 / 第六周作业 / 全量提交记录（虚构测试数据）"],
                ["说明：记录排列按平台内部编号，不代表提交先后；未交与缓考保留原状态"],
                ["账号", "姓名", "班级", "作业成绩(满分100)", "提交时间", "提交编号"]]
    quiz = [["在线测验系统 / 单元测验 / 虚构测试数据"],
            ["工号", "学生姓名", "班级", "测验成绩(满分50)", "提交日期", "场次"]]
    body_lab, body_hw, body_quiz = [], [], []
    for i, s in enumerate(students):
        sid, name, cls = s["id"], s["name"], s["class"]
        for attempt in range(1, 2 + (i % 3 == 0)):
            value = 0 if i == 5 else rng.randint(25, 80)
            raw_id = f" {sid}\u200b " if i == 3 else sid
            body_lab.append([raw_id, name, cls, str(value), f"2026-10-{15+attempt:02d}T09:{i:02d}:00", f"LAB-{i+1:03d}-{attempt}", "重交" if attempt > 1 else "首次提交"])
        state = {8: "未交", 14: "缓考", 20: "免修"}.get(i)
        for attempt in range(1, (2 if state or i % 4 else 3)):
            raw_id = "" if i in (1, 10, 25) else sid
            body_hw.append([raw_id, name, cls, state or str(rng.randint(45, 100)), f"2026-10-{17+attempt:02d}T14:{i:02d}:00", f"HW-{i+1:03d}-{attempt}"])
        for attempt in range(1, (2 if i == 11 or i % 5 else 3)):
            value = "缺考" if i == 11 else str(rng.randint(15, 50))
            body_quiz.append([sid, name, cls, value, f"2026-10-{18+attempt:02d}T16:{i:02d}:00", f"Q-{attempt}"])
    for rows in (body_lab, body_hw, body_quiz):
        rng.shuffle(rows)
    labs += body_lab + [["合计", "", "", "", "", "", ""]]
    homework += body_hw + [["汇总", "", "", "", "", ""]]
    quiz += body_quiz + [["总计", "", "", "", "", ""]]
    files = {"01_教务系统_主花名册.csv": roster, "02_实验平台_全部提交.csv": labs,
             "03_学习平台_作业记录.csv": homework, "04_测验系统_测验记录.csv": quiz}
    ambiguous = copy.deepcopy(files)
    for row in ambiguous["03_学习平台_作业记录.csv"][3:-1]:
        if row[0] == "" and row[1] == "张伟":
            row[2] = ""
    missing_time = copy.deepcopy(files)
    for row in missing_time["03_学习平台_作业记录.csv"][3:-1]:
        if row[0] == students[0]["id"]:
            row[4] = ""
            break
    cases = [("01_完整流程_先试这组", files, "complete"),
             ("02_同名身份歧义_等待确认", ambiguous, "data_checkpoint"),
             ("03_提交时间缺失_等待补充", missing_time, "data_checkpoint")]
    results = []
    validation = ROOT / "outputs" / ("user_trial_validation_" + stamp)
    for title, tables, expected in cases:
        folder = package / title
        folder.mkdir()
        for filename, rows in tables.items():
            save_csv(folder / filename, rows)
        uploads = [(name, (folder / name).read_bytes()) for name in sorted(tables)]
        state = PipelineStore(validation / title).create(TASK, uploads)
        result = {"group": title, "expected": expected, "actual": state["phase"],
                  "counts": (state.get("stage1") or {}).get("counts"), "issues": state["issues"],
                  "csv_data_rows": {name: len(rows) - (4 if name.startswith(("01", "03")) else 3) for name, rows in tables.items()}}
        results.append(result)
        assert state["phase"] == expected, json.dumps(result, ensure_ascii=False)
        if expected == "complete":
            assert state["stage1"]["counts"]["wide_rows"] == 40
            assert state["stage1"]["counts"]["auto_resolved_no_id"] >= 3
        (folder / "任务与预期.txt").write_text("每次只上传本文件夹内4个CSV，不要混用不同组的数据。\n\n可复制任务：\n" + TASK +
            "\n\n预期：" + {"01_完整流程_先试这组": "完成两个阶段，得到40人宽表；未交与缺考按0参与计算，缓考与免修保留状态。",
            "02_同名身份歧义_等待确认": "缺少编号且没有班级的张伟记录应暂停，不能仅凭姓名归到某一个人。原始对应人为0020260002（软件工程2602班），可据此手动确认。",
            "03_提交时间缺失_等待补充": "0020260001的作业存在多次提交，其中一条缺少时间，不能按文件顺序猜最后一次。请在界面查看问题记录。"}[title], encoding="utf-8-sig")
    guide = ("多平台试用数据（全部为程序生成的虚构数据，不含真实学生或联系电话）\n\n"
             "打开 http://127.0.0.1:8765/pipeline\n"
             "1. 从01_完整流程_先试这组开始，选择该文件夹中的全部4个CSV。\n"
             "2. 复制同文件夹任务与预期.txt中的任务到输入框，启动流程。\n"
             "3. 查看真实表头识别、满分折算、实验最高分、作业/测验按时间取最后一次、编号补全和审计。\n"
             "4. 下载阶段一和阶段二结果；缓考/免修应保留，数值0不能当作空值。\n"
             "5. 分别新建流程试用02、03，每次仅选择该组4个CSV，体验确认卡点。\n\n"
             "每组40名虚构学生，教务、实验、学习和测验4种导出，共3组12个CSV。\n"
             "包含顶部说明、底部汇总、不同列名、前导零格式编号、不可见字符、重复提交、乱序时间和特殊状态。\n"
             "同名张伟共两人，班级不同；手机号列SIM-PHONE为测试标识，不是真实号码。\n"
             "建议直接上传CSV，避免Excel自动改写编号或日期。不要把说明TXT作为数据上传。\n"
             "当前未配置真实模型接口；试用依靠程序已公开的字段别名、有限规则解析和确认流程，不代表任意自然语言理解。\n\n"
             "第一组任务：\n" + TASK + "\n")
    (package / "开始试用.txt").write_text(guide, encoding="utf-8-sig")
    validation.mkdir(parents=True, exist_ok=True)
    (validation / "validation.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"package": str(package), "validation": str(validation), "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
