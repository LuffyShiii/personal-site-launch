#!/usr/bin/env python3
"""Read a user's plain-text work description; never edit files or publish anything."""
import argparse
import datetime as dt
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

TEXT = {'title', 'description', 'title.zh', 'title.en', 'description.zh', 'description.en'}
IMAGES = {'.png', '.jpg', '.jpeg', '.webp', '.avif', '.gif', '.svg'}
ALIASES = {}
for key, names in {
    'title': ['标题', '名称', 'title'],
    'description': ['简介', '说明', 'description', 'summary'],
    'title.zh': ['中文标题', 'chinese title'],
    'title.en': ['英文标题', 'english title'],
    'description.zh': ['中文简介', 'chinese description'],
    'description.en': ['英文简介', 'english description'],
    'url': ['链接', '目标链接', '网址', 'link', 'url'],
    'cover': ['封面', '封面文件', 'cover'],
    'date': ['发布日期', '日期', 'date'],
    'featured': ['精选位置', '精选顺序', 'featured'],
    'status': ['发布状态', '状态', 'status'],
}.items():
    for name in names:
        ALIASES[name.casefold()] = key


def inspect(file, languages=('zh',), featured_limit=None):
    file = Path(file)
    issues, fields, seen = [], {}, set()

    def issue(field, message, action):
        issues.append({'field': field, 'message': message, 'action': action})

    def result(state, content=None):
        return {'state': state, 'source': str(file), 'content': content, 'issues': issues,
                'publishes': False}

    if not languages or any(x not in ('zh', 'en') for x in languages) or len(set(languages)) != len(languages):
        issue('languages', '语言设置未被此转换器支持。', '使用 zh、en 或 zh,en；其他语言先扩展映射并测试。')
        return result('needs_input')
    if file.is_symlink() or file.parent.is_symlink():
        issue('file', '说明文件或所在目录是符号链接。', '使用实际素材目录中的普通文件。')
        return result('needs_input')
    try:
        raw = file.read_bytes()
        text = raw.decode('utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig')
    except (OSError, UnicodeError) as exc:
        issue('file', f'无法读取说明文件：{type(exc).__name__}。', '保存为 UTF-8 纯文本说明.txt，并检查文件已复制完成。')
        return result('needs_input')
    current = None
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            if current in TEXT:
                fields[current] += '\n'
            continue
        match = re.match(r'^\s*([^:：]+)[:：](.*)$', line)
        key = ALIASES.get(match.group(1).strip().casefold()) if match else None
        if key:
            # Generic and language-prefixed spellings address the same field.
            if key in ('title', 'description'):
                key += '.' + languages[0]
            if key in seen:
                issue(key, f'第 {number} 行重复填写了同一字段。', '保留一份明确的内容，避免后一项覆盖前一项。')
                current = None
                continue
            seen.add(key)
            fields[key] = match.group(2).strip()
            current = key
        elif current in TEXT:
            fields[current] += '\n' + line.strip()
        else:
            issue('text', f'第 {number} 行无法识别：{line.strip()[:80]}', '按模板保留字段名称和冒号；正文放在简介下面。')
    fields = {k: v.strip() for k, v in fields.items()}
    statuses = {'': 'draft', '草稿': 'draft', 'draft': 'draft', '发布': 'published',
                '已发布': 'published', 'publish': 'published', 'published': 'published',
                '下架': 'withdrawn', 'withdrawn': 'withdrawn'}
    status = statuses.get(fields.get('status', '').casefold())
    if status is None:
        issue('status', '无法识别发布状态。', '填写“草稿”“发布”或“下架”。')
        status = 'invalid'
    item = {'status': status, 'title': {}, 'description': {}, 'url': fields.get('url', ''),
            'cover': fields.get('cover', ''), 'date': fields.get('date') or None, 'featured': None}
    for group in ('title', 'description'):
        for lang in languages:
            item[group][lang] = fields.get(f'{group}.{lang}', '')
            if not item[group][lang] and status != 'withdrawn':
                label = ('中文' if lang == 'zh' else '英文') + ('标题' if group == 'title' else '简介')
                issue(f'{group}.{lang}', f'缺少{label}。', f'在说明文件补充“{label}”；需要翻译时请 Agent 协助审阅。')
    if status != 'withdrawn':
        try:
            u = urlsplit(item['url'])
            valid_url = (u.scheme == 'https' and bool(u.hostname) and not u.username and not u.password
                         and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in item['url']) and '\\' not in item['url'])
            _ = u.port
        except ValueError:
            valid_url = False
        if not valid_url:
            issue('url', '缺少有效的 HTTPS 作品链接。', '复制作品的完整公开链接到“链接”一项；不要填账号密码。')
    if item['date']:
        try:
            if dt.date.fromisoformat(item['date']).isoformat() != item['date']:
                raise ValueError()
        except ValueError:
            issue('date', '日期格式或日期本身无效。', '填写 YYYY-MM-DD；不确定实际日期时可留空。')
    value = fields.get('featured', '').casefold()
    if value not in ('', '不精选', '否', '无', 'no', 'none', 'null'):
        if not re.fullmatch(r'[1-9][0-9]*', value):
            issue('featured', '精选位置必须是正整数或“不精选”。', '按本网站约定填写位置，或留空。')
        else:
            item['featured'] = int(value)
            if featured_limit is not None and item['featured'] > featured_limit:
                issue('featured', f'精选位置超过本网站上限 {featured_limit}。', '使用约定范围内的位置，或选择不精选。')
    if status != 'withdrawn':
        cover = item['cover']
        if cover:
            if '/' in cover or '\\' in cover or cover in ('.', '..'):
                issue('cover', '封面必须是同一作品文件夹里的文件名。', '将图片放进作品文件夹，只填写文件名，不填其他目录路径。')
            else:
                image = file.parent / cover
                if image.is_symlink() or not image.is_file() or image.suffix.casefold() not in IMAGES:
                    issue('cover', '指定封面不存在、不是普通文件或格式不支持。', '核对文件名，放入一张可用图片；中文和空格文件名可以保留。')
        else:
            try:
                candidates = sorted(p.name for p in file.parent.iterdir()
                                    if p.is_file() and not p.is_symlink() and p.suffix.casefold() in IMAGES)
            except OSError:
                issue('cover', '暂时无法读取封面所在目录。', '检查目录权限和文件是否复制完成，再重试。')
                return result('needs_input', item)
            if len(candidates) == 1:
                item['cover'] = candidates[0]
            elif not candidates:
                issue('cover', '缺少封面图片。', '把封面放入说明文件所在文件夹。')
            else:
                issue('cover', '有多张候选封面，无法确定使用哪张。', '在“封面”一项填写要使用的完整文件名。')
    if issues:
        return result('needs_input', item)
    return result({'published': 'ready', 'draft': 'draft', 'withdrawn': 'withdrawn'}[status], item)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', type=Path)
    parser.add_argument('--languages', default='zh')
    parser.add_argument('--featured-limit', type=int)
    args = parser.parse_args()
    if args.featured_limit is not None and args.featured_limit < 1:
        parser.error('--featured-limit must be positive')
    outcome = inspect(args.file, tuple(x.strip() for x in args.languages.split(',')), args.featured_limit)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    return 2 if outcome['state'] == 'needs_input' else 0


if __name__ == '__main__':
    sys.exit(main())
