import os
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True


ROOT = Path(__file__).resolve().parent
DEBUG_ROOT = ROOT / "debug_runs"
BATCH_MAX_ATTEMPTS = int(os.environ.get("BOC_BATCH_MAX_ATTEMPTS", "3"))
BATCH_RETRY_WAIT_SECONDS = int(os.environ.get("BOC_BATCH_RETRY_WAIT_SECONDS", "30"))
BATCH_BETWEEN_JOBS_SECONDS = int(os.environ.get("BOC_BATCH_BETWEEN_JOBS_SECONDS", "15"))


# 占位/示例数据，不是真实收款方。真实批量截图制单前由操作人替换 JOBS，
# 切勿把真实账号/户名/金额提交进版本库。
JOBS = [
    {
        "source": "sample_01.png",
        "payee": "示例收款方公司一",
        "account": "000000000000000",
        "bank": "示例银行示例支行",
        "amount": "0.01",
    },
    {
        "source": "sample_02.png",
        "payee": "示例收款方公司二",
        "account": "000000000000000",
        "bank": "示例银行示例支行",
        "amount": "0.01",
    },
]


def latest_run_dir() -> Path | None:
    if not DEBUG_ROOT.is_dir():
        return None
    dirs = [p for p in DEBUG_ROOT.iterdir() if p.is_dir()]
    if not dirs:
        return None
    return max(dirs, key=lambda p: p.stat().st_mtime)


def is_retryable_session_failure(log_text: str) -> bool:
    markers = (
        "会话已失效",
        "其它浏览器登录",
        "其他浏览器登录",
        "企业网银页面：https://netc2.igtb.boc.cn/#/login-page",
        "转账表单预填失败_未提交 url=https://netc2.igtb.boc.cn/#/login-page",
        "转账汇款表单填写不完整 url=https://netc2.igtb.boc.cn/#/login-page",
    )
    return any(marker in log_text for marker in markers)


def run_job(index: int, job: dict[str, str]) -> tuple[bool, Path | None]:
    env = os.environ.copy()
    env.update(
        {
            "BOC_ENABLE_TRANSFER_FILL": "1",
            "BOC_ENABLE_ORDER_SUBMIT": "0",
            "BOC_CLOSE_ON_FINISH": "1",
            "BOC_USBHUB_POWER_ON_START": "1",
            "BOC_USBHUB_ALL_OFF_ON_FINISH": "1",
            "BOC_PAYEE_NAME": job["payee"],
            "BOC_PAYEE_ACCOUNT": job["account"],
            "BOC_PAYEE_BANK": job["bank"],
            "BOC_PAYEE_BANK_CODE": "",
            "BOC_PAYMENT_AMOUNT": job["amount"],
        }
    )
    last_run_dir = None
    for attempt in range(1, max(1, BATCH_MAX_ATTEMPTS) + 1):
        print(
            f"\n========== 批量 {index:02d}/10 第 {attempt} 次 {job['source']} ==========",
            flush=True,
        )
        print(
            f"{job['payee']} | {job['account']} | {job['bank']} | {job['amount']}",
            flush=True,
        )
        completed = subprocess.run(
            [sys.executable, "open_boc.py"],
            cwd=str(ROOT),
            env=env,
            text=True,
            check=False,
        )
        run_dir = latest_run_dir()
        last_run_dir = run_dir
        ok = completed.returncode == 0
        retryable = False
        if run_dir is not None:
            log_path = run_dir / "run.log"
            try:
                log_text = log_path.read_text(encoding="utf-8", errors="ignore")
                ok = ok and "[完成] 转账表单必填项已填写（未提交）" in log_text
                retryable = (not ok) and is_retryable_session_failure(log_text)
            except Exception:
                ok = False

        if ok:
            print(
                f"[批量结果] {index:02d}/10 成功 attempt={attempt} run={run_dir}",
                flush=True,
            )
            time.sleep(BATCH_BETWEEN_JOBS_SECONDS)
            return True, run_dir

        print(
            f"[批量结果] {index:02d}/10 失败 attempt={attempt} retryable={retryable} run={run_dir}",
            flush=True,
        )
        if retryable and attempt < BATCH_MAX_ATTEMPTS:
            print(
                f"[批量重试] 检测到银行会话失效/跳回登录页，等待 {BATCH_RETRY_WAIT_SECONDS}s 后重试",
                flush=True,
            )
            time.sleep(BATCH_RETRY_WAIT_SECONDS)
            continue
        break

    time.sleep(BATCH_BETWEEN_JOBS_SECONDS)
    return False, last_run_dir


def main() -> int:
    selected = []
    if len(sys.argv) > 1:
        wanted = {int(arg) for arg in sys.argv[1:]}
        selected = [(idx, job) for idx, job in enumerate(JOBS, 1) if idx in wanted]
    else:
        selected = list(enumerate(JOBS, 1))

    results = []
    for index, job in selected:
        ok, run_dir = run_job(index, job)
        results.append((index, job, ok, run_dir))

    print("\n========== 批量汇总 ==========", flush=True)
    for index, job, ok, run_dir in results:
        print(
            f"{index:02d}. {'成功' if ok else '失败'} | {job['source']} | {job['payee']} | {run_dir}",
            flush=True,
        )
    return 0 if all(ok for _, _, ok, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
