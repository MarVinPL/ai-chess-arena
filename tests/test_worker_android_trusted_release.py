"""Static trust-boundary and executable preflight tests, no network or keys."""
import json
import os
from pathlib import Path
import subprocess
import shutil
import pytest
import re
import textwrap

WF = Path(__file__).resolve().parents[1] / ".github/workflows/worker-android-trusted-release.yml"
SOURCE = WF.read_text()
BUILD = SOURCE.split("\n  build:",1)[1].split("\n  sign:",1)[0]
SIGN = SOURCE.split("\n  sign:",1)[1].split("\n  publish:",1)[0]
PUBLISH = SOURCE.split("\n  publish:",1)[1]
def scripts(section):
    return [textwrap.dedent(x) for x in re.findall(r"^        run: \|\n((?: {10}[^\n]*\n|\n)+)", section, re.M)]
SHA = "a" * 40
REPO = "MarVinPL/ai-chess-arena"

def test_trust_boundaries():
    assert "\n  workflow_dispatch:" in SOURCE
    assert not re.search(r"^  (push|pull_request|pull_request_target):", SOURCE, re.M)
    assert "\npermissions: {}" in SOURCE
    for job in (BUILD,SIGN,PUBLISH):
        assert "refs/heads/main" in job
        assert "runs-on: ubuntu-24.04" in job
        assert scripts(job)
        for script in scripts(job):
            subprocess.run(["bash", "-n"], input=script, text=True, check=True)
    assert "environment: android-signing" in SIGN
    assert "needs: build" in SIGN
    assert "needs: sign" in PUBLISH
    assert "secrets." not in BUILD and "secrets." not in PUBLISH
    assert "actions/checkout" not in SIGN and "actions/checkout" not in PUBLISH
    assert "contents: write" in PUBLISH
    assert "persist-credentials: false" in WF.read_text()
    assert WF.read_text().count("3f0449ec3a2399ec669cce2dcbd6aa7262671345dfb983c33e2cadf4d1b0da12") == 2

@pytest.mark.skipif(not shutil.which("jq"), reason="jq required for executable preflight")
@pytest.mark.parametrize("channel,case,ok", [
    ("Pre-release","valid",True), ("Latest","valid",True),
    ("Pre-release","bad_sha",False), ("Pre-release","fork",False),
    ("Pre-release","closed",False), ("Pre-release","bad_base",False),
    ("Latest","unmerged",False), ("Latest","old_main",False),
    ("Pre-release","failed_ci",False), ("Pre-release","skipped_job",False),
    ("Pre-release","wrong_ci_sha",False), ("Pre-release","existing_release",False),
    ("Pre-release","existing_tag",False), ("Pre-release","api_error",False),
    ("Pre-release","bad_version",False), ("Pre-release","bad_pr",False),
    ("Pre-release","bad_code",False), ("Pre-release","bad_test",False),
    ("Other","valid",False), ("Latest","failed_ci",False),
])
def test_preflight(tmp_path,channel,case,ok):
    gh = tmp_path / "gh"
    gh.write_text("""#!/usr/bin/env python3
import os,sys,json
a=sys.argv[1:]; c=os.environ['CASE']; sha='a'*40
channel=os.environ['RELEASE_CHANNEL']
if '/pulls/' in a[1]:
    result={'state':'closed' if c=='closed' else 'open','head':{'sha':sha,'repo':{'full_name':'fork/repo' if c=='fork' else 'MarVinPL/ai-chess-arena'}},'base':{'ref':'other' if c=='bad_base' else 'main'},'merged':channel=='Latest' and c!='unmerged','merge_commit_sha':sha}
elif any('/actions/workflows/' in x for x in a):
    event=next(x.split('=',1)[1] for x in a if x.startswith('event='))
    result={'workflow_runs':[{'head_sha':'b'*40 if c=='wrong_ci_sha' else sha,'event':event,'status':'completed','conclusion':'failure' if c=='failed_ci' else 'success','id':1}]}
elif any('/actions/runs/' in x for x in a):
    result={'jobs':[{'name':j,'conclusion':'skipped' if c=='skipped_job' else 'success'} for j in ('tests','android / apk','android_signed / apk')]}
elif any('/releases?' in x for x in a):
    if c=='api_error': sys.exit(1)
    result=[{'tag_name':'worker-v0.1.3-test.5'}] if c=='existing_release' else []
elif any('/matching-refs/' in x for x in a):
    if c=='existing_tag': print('refs/tags/worker-v0.1.3-test.5')
    sys.exit(0)
else: sys.exit(2)
print(json.dumps(result))
""")
    gh.chmod(0o755)
    env=dict(os.environ,PATH=str(tmp_path)+":"+os.environ["PATH"],CASE=case, RELEASE_CHANNEL=channel,GITHUB_REPOSITORY=REPO,GITHUB_SHA="b"*40 if case=="old_main" else SHA,SOURCE_SHA="bad" if case=="bad_sha" else SHA,PR_NUMBER="1;echo bad" if case=="bad_pr" else "197",APP_VERSION="0.1.3;bad" if case=="bad_version" else "0.1.3",VERSION_CODE="0" if case=="bad_code" else "1003",TEST_NUMBER="../" if case=="bad_test" else "5")
    r=subprocess.run(["bash","-c",scripts(BUILD)[0]],env=env,capture_output=True,text=True)
    assert (r.returncode==0)==ok,(r.returncode,r.stdout,r.stderr)
