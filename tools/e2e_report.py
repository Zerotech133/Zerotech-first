import os
import json
import re
from datetime import datetime

def scan_e2e_traces():
    # 定位主仓库根目录
    if os.path.exists('.git'):
        root_dir = os.path.join(os.path.abspath('.'), os.path.dirname(os.path.abspath('.git')))
    else:
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))

    # 扫描 data/e2e/ 目录下的所有 K-*_trace_*.md 文件
    trace_files = []
    for root, dirs, files in os.walk(os.path.join(root_dir, 'data', 'e2e')):
        for file in files:
            if re.match(r'K-.*_trace_.*\.md', file):
                trace_files.append(os.path.join(root, file))

    return trace_files

def parse_trace_file(trace_file):
    with open(trace_file, 'r', encoding='utf-8') as file:
        lines = file.readlines()

    # 解析文件名获取 TID 和时间
    tid = re.match(r'K-(\w+)_trace_(\d{8})', os.path.basename(trace_file)).group(1)
    time = datetime.strptime(re.match(r'K-(\w+)_trace_(\d{8})', os.path.basename(trace_file)).group(2), '%Y%m%d')

    # 提取终态
    final_state = re.search(r'\[(DONE|ABORT|SEC2_FAIL|QA1_FAIL)\]', lines[0]).group(1)

    # 提取步骤数
    steps = len([line for line in lines if line.startswith('- [')])

    # 提取最后一步内容
    last_step = lines[-1].strip()

    return {
        'tid': tid,
        'time': time,
        'final': final_state,
        'steps': steps,
        'last_step': last_step
    }

def generate_e2e_report():
    trace_files = scan_e2e_traces()
    report_data = []

    for trace_file in trace_files:
        report_data.append(parse_trace_file(trace_file))

    # 按时间倒序排序
    report_data.sort(key=lambda x: x['time'], reverse=True)

    # 输出 JSON 对象
    print(json.dumps({'runs': len(report_data), 'items': report_data}, ensure_ascii=False))

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Generate E2E report from trace files.')
    parser.add_argument('--json', action='store_true', help='Output the report in JSON format.')
    args = parser.parse_args()

    if args.json:
        generate_e2e_report()
        exit(0)
    else:
        print("Invalid argument. Use --json to output the report in JSON format.")
        exit(1)