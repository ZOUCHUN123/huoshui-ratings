#!/usr/bin/env python3
"""
活水教师评价数据抓取与本地页面生成器
从 api.huoshui.org 抓取所有教师评分数据和评价文本，生成本地可离线查询的 HTML 页面。
"""

import json
import time
import urllib.request
import urllib.error
import os
import sys
from datetime import datetime

API_BASE = "https://api.huoshui.org"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(SCRIPT_DIR, "profs_data.json")
HTML_FILE = os.path.join(SCRIPT_DIR, "index.html")

MAX_ID = 3000
CONSECUTIVE_404_LIMIT = 80
DELAY = 0.3
BATCH_SIZE = 50
BATCH_PAUSE = 3
MAX_RETRIES = 3


def fetch_json(url, retries=MAX_RETRIES):
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                wait = 20 * (attempt + 1)
                print(f"    429 限流，等待 {wait}s 后重试...", flush=True)
                time.sleep(wait)
                continue
            raise
    raise Exception("max retries exceeded")


def fetch_all_profs():
    profs = []
    consecutive_404 = 0
    rate_limit_hits = 0
    since_last_pause = 0

    for prof_id in range(1, MAX_ID + 1):
        url = f"{API_BASE}/profs/{prof_id}"
        try:
            result = fetch_json(url)
            data = result.get("data")
            if data and data.get("name"):
                profs.append(data)
                consecutive_404 = 0
                if len(profs) % 100 == 0:
                    print(f"  已获取 {len(profs)} 位教师 (当前ID: {prof_id})", flush=True)
            else:
                consecutive_404 += 1
        except urllib.error.HTTPError as e:
            if e.code == 404:
                consecutive_404 += 1
            elif e.code == 429:
                rate_limit_hits += 1
                consecutive_404 += 1
                if rate_limit_hits > 30:
                    print(f"  限流次数过多({rate_limit_hits})，停止抓取", flush=True)
                    break
            else:
                print(f"  ID {prof_id} 错误: HTTP {e.code}", flush=True)
                consecutive_404 += 1
        except Exception as e:
            print(f"  ID {prof_id} 异常: {e}", flush=True)
            consecutive_404 += 1

        if consecutive_404 >= CONSECUTIVE_404_LIMIT:
            print(f"  连续 {CONSECUTIVE_404_LIMIT} 个无数据，停止抓取 (当前ID: {prof_id})", flush=True)
            break

        since_last_pause += 1
        if since_last_pause >= BATCH_SIZE:
            time.sleep(BATCH_PAUSE)
            since_last_pause = 0
        else:
            time.sleep(DELAY)

    return profs


def fetch_all_reviews(profs):
    reviews_by_prof = {}
    profs_with_reviews = [p for p in profs if p.get("stats", {}).get("reviewCount", 0) > 0]
    total = len(profs_with_reviews)
    print(f"  共 {total} 位教师有评价，开始抓取...", flush=True)

    consecutive_404 = 0
    rate_limit_hits = 0
    since_last_pause = 0
    count = 0

    for p in profs_with_reviews:
        prof_id = p["id"]
        url = f"{API_BASE}/profs/{prof_id}/reviews"
        try:
            result = fetch_json(url)
            data = result.get("data", [])
            if data:
                simplified = []
                for r in data:
                    course = r.get("course", {})
                    author = r.get("author", {})
                    tags = [{"name": t.get("name", ""), "polarity": t.get("polarity", "neutral")} for t in r.get("tags", [])]
                    simplified.append({
                        "comment": r.get("comment", ""),
                        "courseName": course.get("name", ""),
                        "rateProfessional": r.get("rateProfessional"),
                        "rateExpressive": r.get("rateExpressive"),
                        "rateKind": r.get("rateKind"),
                        "rateHomework": r.get("rateHomework"),
                        "rateAttend": r.get("rateAttend"),
                        "rateBirdy": r.get("rateBirdy"),
                        "upvoteCount": r.get("upvoteCount", 0),
                        "downvoteCount": r.get("downvoteCount", 0),
                        "authorName": author.get("username", ""),
                        "authorYear": author.get("firstYear"),
                        "tags": tags,
                        "createdAt": r.get("createdAt", ""),
                    })
                reviews_by_prof[prof_id] = simplified
                count += len(simplified)
                consecutive_404 = 0
            else:
                consecutive_404 += 1

            if len(reviews_by_prof) % 100 == 0 and len(reviews_by_prof) > 0:
                print(f"  已抓取 {len(reviews_by_prof)}/{total} 位教师评价，共 {count} 条", flush=True)

        except urllib.error.HTTPError as e:
            if e.code == 404:
                consecutive_404 += 1
            elif e.code == 429:
                rate_limit_hits += 1
                consecutive_404 += 1
                if rate_limit_hits > 30:
                    print(f"  限流次数过多({rate_limit_hits})，停止抓取", flush=True)
                    break
            else:
                print(f"  评价 ID {prof_id} 错误: HTTP {e.code}", flush=True)
                consecutive_404 += 1
        except Exception as e:
            print(f"  评价 ID {prof_id} 异常: {e}", flush=True)
            consecutive_404 += 1

        since_last_pause += 1
        if since_last_pause >= BATCH_SIZE:
            time.sleep(BATCH_PAUSE)
            since_last_pause = 0
        else:
            time.sleep(DELAY)

    print(f"  评价抓取完成：{len(reviews_by_prof)} 位教师，共 {count} 条评价", flush=True)
    return reviews_by_prof


def fetch_tags():
    try:
        result = fetch_json(f"{API_BASE}/tags")
        return result.get("data", [])
    except Exception as e:
        print(f"  标签获取失败: {e}")
        return []


def generate_html(profs, tags, reviews_by_prof, fetch_time):
    tags_map = {t["id"]: t for t in tags}

    def tag_name(tag_id):
        t = tags_map.get(tag_id)
        return t["name"] if t else ""

    def tag_polarity(tag_id):
        t = tags_map.get(tag_id)
        return t["polarity"] if t else "neutral"

    simplified = []
    for p in profs:
        stats = p.get("stats", {})
        dept = p.get("dept", {})
        prof_id = p.get("id")
        top_tags = []
        for tt in p.get("topTags", []):
            top_tags.append({
                "name": tt.get("name", tag_name(tt.get("id"))),
                "polarity": tt.get("polarity", tag_polarity(tt.get("id"))),
                "count": tt.get("count", 0),
            })

        prof_reviews = reviews_by_prof.get(prof_id, [])
        course_names = sorted(set(r["courseName"] for r in prof_reviews if r["courseName"]))

        simplified.append({
            "id": prof_id,
            "name": p.get("name", ""),
            "dept": dept.get("fullname", ""),
            "deptShort": dept.get("shortname", ""),
            "reviewCount": stats.get("reviewCount", 0),
            "goodCount": stats.get("goodReviewCount", 0),
            "badCount": stats.get("badReviewCount", 0),
            "avgCount": stats.get("avgReviewCount", 0),
            "rateOverall": stats.get("rateOverall", 0),
            "rateProfessional": stats.get("rateProfessional", 0),
            "rateExpress": stats.get("rateExpress", 0),
            "rateKind": stats.get("rateKind", 0),
            "attendanceOverall": stats.get("attendanceOverall", 0),
            "attendanceCount": stats.get("attendanceCount", 0),
            "homeworkOverall": stats.get("homeworkOverall", 0),
            "homeworkCount": stats.get("homeworkCount", 0),
            "birdyOverall": stats.get("birdyOverall", 0),
            "birdyCount": stats.get("birdyCount", 0),
            "examCount": stats.get("examCount", 0),
            "topTags": top_tags,
            "courseNames": course_names,
            "reviewCountActual": len(prof_reviews),
        })

    data_json = json.dumps(simplified, ensure_ascii=False)
    reviews_json = json.dumps(reviews_by_prof, ensure_ascii=False)

    html = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>活水教师评分 - 在线查询</title>
<style>
:root {
  --bg: #f0f2f5;
  --card: #ffffff;
  --text: #1a1a2e;
  --text-secondary: #666;
  --primary: #1890ff;
  --primary-light: #e6f7ff;
  --border: #e8e8e8;
  --positive: #52c41a;
  --negative: #f5222d;
  --warning: #faad14;
  --shadow: 0 2px 8px rgba(0,0,0,0.08);
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  background: var(--bg);
  color: var(--text);
  min-height: 100vh;
}
.header {
  background: var(--card);
  padding: 20px 24px;
  box-shadow: var(--shadow);
  position: sticky;
  top: 0;
  z-index: 100;
}
.header-inner { max-width: 960px; margin: 0 auto; }
.header h1 { font-size: 22px; font-weight: 700; margin-bottom: 4px; }
.header .subtitle { font-size: 13px; color: var(--text-secondary); margin-bottom: 16px; }
.search-box { display: flex; gap: 8px; flex-wrap: wrap; }
.search-input {
  flex: 1; min-width: 200px; padding: 10px 16px; font-size: 15px;
  border: 2px solid var(--border); border-radius: 8px; outline: none; transition: border-color 0.2s;
}
.search-input:focus { border-color: var(--primary); }
.dept-select, .sort-select {
  padding: 10px 12px; font-size: 14px; border: 2px solid var(--border);
  border-radius: 8px; background: var(--card); cursor: pointer; outline: none;
}
.dept-select:focus, .sort-select:focus { border-color: var(--primary); }
.container { max-width: 960px; margin: 0 auto; padding: 20px 24px; }
.stats-bar {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 16px; font-size: 13px; color: var(--text-secondary);
}
.results { display: flex; flex-direction: column; gap: 12px; }
.prof-card {
  background: var(--card); border-radius: 10px; padding: 18px 20px;
  box-shadow: var(--shadow); transition: box-shadow 0.15s; cursor: pointer;
}
.prof-card:hover { box-shadow: 0 4px 16px rgba(0,0,0,0.12); }
.card-top {
  display: flex; justify-content: space-between; align-items: flex-start;
  margin-bottom: 14px; gap: 12px;
}
.prof-info h3 { font-size: 17px; font-weight: 700; margin-bottom: 2px; }
.prof-info .dept-tag {
  font-size: 12px; color: var(--primary); background: var(--primary-light);
  padding: 2px 8px; border-radius: 4px;
}
.score-box { text-align: center; min-width: 64px; }
.score-box .score { font-size: 28px; font-weight: 700; line-height: 1; }
.score-box .score-label { font-size: 11px; color: var(--text-secondary); margin-top: 2px; }
.score-good { color: var(--positive); }
.score-mid { color: var(--warning); }
.score-bad { color: var(--negative); }
.sub-scores {
  display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px;
  margin-bottom: 12px; padding: 10px 0;
  border-top: 1px solid var(--border); border-bottom: 1px solid var(--border);
}
.sub-score-item { text-align: center; }
.sub-score-item .label { font-size: 11px; color: var(--text-secondary); margin-bottom: 2px; }
.sub-score-item .value { font-size: 15px; font-weight: 600; }
.bar-container {
  width: 100%; height: 4px; background: var(--border);
  border-radius: 2px; margin-top: 4px; overflow: hidden;
}
.bar-fill { height: 100%; border-radius: 2px; transition: width 0.4s ease; }
.tags-row { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
.tag-badge {
  font-size: 12px; padding: 2px 8px; border-radius: 4px;
  display: inline-flex; align-items: center; gap: 4px;
}
.tag-badge .count { font-size: 10px; opacity: 0.7; }
.tag-positive { background: #f6ffed; color: var(--positive); border: 1px solid #b7eb8f; }
.tag-negative { background: #fff1f0; color: var(--negative); border: 1px solid #ffa39e; }
.tag-neutral { background: #f5f5f5; color: var(--text-secondary); border: 1px solid var(--border); }
.meta-row { display: flex; gap: 16px; font-size: 12px; color: var(--text-secondary); flex-wrap: wrap; }
.meta-item span { font-weight: 600; color: var(--text); }
.expand-hint {
  font-size: 12px; color: var(--primary); margin-top: 8px;
  display: flex; align-items: center; gap: 4px;
}
.expand-hint .arrow { transition: transform 0.2s; display: inline-block; }
.prof-card.expanded .expand-hint .arrow { transform: rotate(180deg); }
.reviews-section {
  display: none; margin-top: 12px; padding-top: 12px;
  border-top: 1px solid var(--border);
}
.reviews-section.show { display: block; }
.review-item {
  padding: 12px 0; border-bottom: 1px solid #f0f0f0;
}
.review-item:last-child { border-bottom: none; }
.review-header {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 6px; font-size: 12px; color: var(--text-secondary);
}
.review-course {
  background: var(--primary-light); color: var(--primary);
  padding: 1px 6px; border-radius: 3px; font-size: 12px;
}
 review-author { color: var(--text-secondary); }
.review-comment {
  font-size: 14px; line-height: 1.6; margin-bottom: 8px; word-break: break-word;
}
.review-scores {
  display: flex; gap: 12px; flex-wrap: wrap; font-size: 12px; margin-bottom: 6px;
}
.review-score-item { color: var(--text-secondary); }
.review-score-item span { font-weight: 600; color: var(--text); }
.review-tags { display: flex; gap: 4px; flex-wrap: wrap; margin-bottom: 4px; }
.review-footer {
  display: flex; gap: 12px; font-size: 12px; color: var(--text-secondary);
}
.review-vote { display: inline-flex; align-items: center; gap: 3px; }
.no-result { text-align: center; padding: 60px 20px; color: var(--text-secondary); }
.no-result .icon { font-size: 48px; margin-bottom: 12px; }
.footer { text-align: center; padding: 24px; font-size: 12px; color: var(--text-secondary); }
.loading-text { text-align: center; padding: 20px; color: var(--text-secondary); font-size: 13px; }
@media (max-width: 600px) {
  .sub-scores { grid-template-columns: 1fr 1fr 1fr; }
  .card-top { flex-direction: column; }
  .score-box { text-align: left; }
}
</style>
</head>
<body>
<div class="header">
  <div class="header-inner">
    <h1>活水教师评分 · 在线查询</h1>
    <div class="subtitle">数据来源：api.huoshui.org · 每日自动更新，选课期间随时可用</div>
    <div class="search-box">
      <input type="text" class="search-input" id="searchInput" placeholder="输入教师姓名或课程名称..." autocomplete="off">
      <select class="dept-select" id="deptFilter">
        <option value="">全部学院</option>
      </select>
      <select class="sort-select" id="sortBy">
        <option value="rateOverall">按综合评分</option>
        <option value="reviewCount">按评价数量</option>
        <option value="name">按姓名</option>
      </select>
    </div>
  </div>
</div>
<div class="container">
  <div class="stats-bar">
    <span id="resultCount">加载中...</span>
    <span id="updateTime"></span>
  </div>
  <div class="results" id="results"></div>
</div>
<div class="footer">
  数据仅供选课参考，请理性看待评价内容<br>
  本工具每日自动更新缓存数据，不修改、不上传任何数据
</div>
<script>
const PROF_DATA = __DATA_JSON__;
const REVIEWS_DATA = __REVIEWS_JSON__;
const FETCH_TIME = "__FETCH_TIME__";

function init() {
  const deptFilter = document.getElementById('deptFilter');
  const depts = [...new Set(PROF_DATA.map(p => p.dept).filter(Boolean))];
  const collator = new Intl.Collator('zh-CN');
  depts.sort((a, b) => collator.compare(a, b));
  depts.forEach(d => {
    const opt = document.createElement('option');
    opt.value = d; opt.textContent = d;
    deptFilter.appendChild(opt);
  });

  document.getElementById('updateTime').textContent = '数据更新: ' + FETCH_TIME;
  document.getElementById('searchInput').addEventListener('input', render);
  deptFilter.addEventListener('change', render);
  document.getElementById('sortBy').addEventListener('change', render);
  document.getElementById('searchInput').focus();
  render();
}

function render() {
  const query = document.getElementById('searchInput').value.trim().toLowerCase();
  const dept = document.getElementById('deptFilter').value;
  const sortBy = document.getElementById('sortBy').value;

  let list = PROF_DATA.filter(p => {
    if (dept && p.dept !== dept) return false;
    if (query) {
      const haystack = (p.name + ' ' + (p.courseNames || []).join(' ')).toLowerCase();
      if (!haystack.includes(query)) return false;
    }
    return true;
  });

  if (sortBy === 'rateOverall') {
    list.sort((a, b) => (b.rateOverall || 0) - (a.rateOverall || 0));
  } else if (sortBy === 'reviewCount') {
    list.sort((a, b) => (b.reviewCount || 0) - (a.reviewCount || 0));
  } else if (sortBy === 'name') {
    const c = new Intl.Collator('zh-CN');
    list.sort((a, b) => c.compare(a.name || '', b.name || ''));
  }

  document.getElementById('resultCount').textContent = `共 ${list.length} 位教师`;
  const container = document.getElementById('results');
  if (list.length === 0) {
    container.innerHTML = '<div class="no-result"><div class="icon">\uD83D\uDD0D</div>未找到匹配的教师<br>试试其他关键词</div>';
    return;
  }

  const shown = list.slice(0, 100);
  container.innerHTML = shown.map(p => renderCard(p)).join('');
  if (list.length > 100) {
    container.innerHTML += '<div class="no-result" style="padding:20px;">显示前 100 条结果，请缩小搜索范围...</div>';
  }
}

function scoreClass(score) {
  if (score >= 4) return 'score-good';
  if (score >= 3) return 'score-mid';
  return 'score-bad';
}
function barColor(score) {
  if (score >= 4) return '#52c41a';
  if (score >= 3) return '#faad14';
  if (score > 0) return '#f5222d';
  return '#d9d9d9';
}
function fmtScore(s) { return s > 0 ? s.toFixed(2) : '—'; }

function renderCard(p) {
  const sc = scoreClass(p.rateOverall);
  const tagsHtml = (p.topTags || []).map(t => {
    const cls = t.polarity === 'positive' ? 'tag-positive' : t.polarity === 'negative' ? 'tag-negative' : 'tag-neutral';
    return `<span class="tag-badge ${cls}">${t.name}<span class="count">${t.count}</span></span>`;
  }).join('');

  const profBar = barColor(p.rateProfessional);
  const expressBar = barColor(p.rateExpress);
  const kindBar = barColor(p.rateKind);
  const profPct = (p.rateProfessional / 5 * 100).toFixed(0);
  const expressPct = (p.rateExpress / 5 * 100).toFixed(0);
  const kindPct = (p.rateKind / 5 * 100).toFixed(0);

  const hasReviews = REVIEWS_DATA[p.id] && REVIEWS_DATA[p.id].length > 0;
  const reviewHint = hasReviews
    ? `<div class="expand-hint">点击查看 ${REVIEWS_DATA[p.id].length} 条评价 <span class="arrow">\u25BC</span></div>`
    : '';

  return `
  <div class="prof-card" data-pid="${p.id}">
    <div class="card-top">
      <div class="prof-info">
        <h3>${p.name}</h3>
        <span class="dept-tag">${p.deptShort || p.dept}</span>
      </div>
      <div class="score-box">
        <div class="score ${sc}">${fmtScore(p.rateOverall)}</div>
        <div class="score-label">综合评分</div>
      </div>
    </div>
    <div class="sub-scores">
      <div class="sub-score-item">
        <div class="label">专业</div>
        <div class="value">${fmtScore(p.rateProfessional)}</div>
        <div class="bar-container"><div class="bar-fill" data-width="${profPct}%" style="width:0;background:${profBar}"></div></div>
      </div>
      <div class="sub-score-item">
        <div class="label">表达</div>
        <div class="value">${fmtScore(p.rateExpress)}</div>
        <div class="bar-container"><div class="bar-fill" data-width="${expressPct}%" style="width:0;background:${expressBar}"></div></div>
      </div>
      <div class="sub-score-item">
        <div class="label">耐心</div>
        <div class="value">${fmtScore(p.rateKind)}</div>
        <div class="bar-container"><div class="bar-fill" data-width="${kindPct}%" style="width:0;background:${kindBar}"></div></div>
      </div>
    </div>
    ${tagsHtml ? `<div class="tags-row">${tagsHtml}</div>` : ''}
    <div class="meta-row">
      <span class="meta-item">评价 <span>${p.reviewCount}</span></span>
      <span class="meta-item">好评 <span style="color:#52c41a">${p.goodCount}</span></span>
      <span class="meta-item">差评 <span style="color:#f5222d">${p.badCount}</span></span>
      ${p.attendanceCount > 0 ? `<span class="meta-item">点名 <span>${p.attendanceOverall.toFixed(1)}</span></span>` : ''}
      ${p.homeworkCount > 0 ? `<span class="meta-item">作业 <span>${p.homeworkOverall.toFixed(1)}</span></span>` : ''}
      ${p.birdyCount > 0 ? `<span class="meta-item">水课 <span>${p.birdyOverall.toFixed(1)}</span></span>` : ''}
      ${p.examCount > 0 ? `<span class="meta-item">考试 <span>${p.examCount}条</span></span>` : ''}
    </div>
    ${reviewHint}
    <div class="reviews-section" id="reviews-${p.id}"></div>
  </div>`;
}

function toggleReviews(card) {
  const pid = parseInt(card.dataset.pid);
  const section = document.getElementById('reviews-' + pid);
  const isExpanded = card.classList.contains('expanded');

  if (isExpanded) {
    card.classList.remove('expanded');
    section.classList.remove('show');
    return;
  }

  card.classList.add('expanded');
  section.classList.add('show');

  if (section.dataset.loaded) return;
  section.dataset.loaded = '1';

  const reviews = REVIEWS_DATA[pid];
  if (!reviews || reviews.length === 0) {
    section.innerHTML = '<div class="loading-text">暂无评价</div>';
    return;
  }

  const c = new Intl.Collator('zh-CN');
  const sorted = [...reviews].sort((a, b) => (b.createdAt || '').localeCompare(a.createdAt || ''));

  section.innerHTML = sorted.map(r => {
    const scores = [];
    if (r.rateProfessional) scores.push(`<span class="review-score-item">专业 <span>${r.rateProfessional}</span></span>`);
    if (r.rateExpressive) scores.push(`<span class="review-score-item">表达 <span>${r.rateExpressive}</span></span>`);
    if (r.rateKind) scores.push(`<span class="review-score-item">耐心 <span>${r.rateKind}</span></span>`);
    if (r.rateHomework) scores.push(`<span class="review-score-item">作业 <span>${r.rateHomework}</span></span>`);
    if (r.rateAttend) scores.push(`<span class="review-score-item">点名 <span>${r.rateAttend}</span></span>`);
    if (r.rateBirdy) scores.push(`<span class="review-score-item">水课 <span>${r.rateBirdy}</span></span>`);

    const tagsHtml = (r.tags || []).map(t => {
      const cls = t.polarity === 'positive' ? 'tag-positive' : t.polarity === 'negative' ? 'tag-negative' : 'tag-neutral';
      return `<span class="tag-badge ${cls}">${t.name}</span>`;
    }).join('');

    const date = r.createdAt ? r.createdAt.substring(0, 10) : '';
    const author = r.authorName ? `${r.authorName}${r.authorYear ? ' (' + r.authorYear + '级)' : ''}` : '匿名';

    return `
    <div class="review-item">
      <div class="review-header">
        <span class="review-course">${r.courseName || '未知课程'}</span>
        <span class="review-author">${author} · ${date}</span>
      </div>
      <div class="review-comment">${(r.comment || '').replace(/</g, '&lt;')}</div>
      ${scores.length ? `<div class="review-scores">${scores.join('')}</div>` : ''}
      ${tagsHtml ? `<div class="review-tags">${tagsHtml}</div>` : ''}
      <div class="review-footer">
        <span class="review-vote">\uD83D\uDC4D ${r.upvoteCount}</span>
        <span class="review-vote">\uD83D\uDC4E ${r.downvoteCount}</span>
      </div>
    </div>`;
  }).join('');
}

document.addEventListener('click', function(e) {
  const card = e.target.closest('.prof-card');
  if (card && !e.target.closest('.reviews-section')) {
    toggleReviews(card);
  }
});

requestAnimationFrame(() => {
  document.querySelectorAll('.bar-fill').forEach(el => { el.style.width = el.dataset.width; });
});

init();
</script>
</body>
</html>"""

    html = html.replace("__DATA_JSON__", data_json)
    html = html.replace("__REVIEWS_JSON__", reviews_json)
    html = html.replace("__FETCH_TIME__", fetch_time)

    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)


def main():
    sys.stdout.reconfigure(line_buffering=True)
    print("=" * 50, flush=True)
    print("活水教师评价数据抓取器", flush=True)
    print("=" * 50, flush=True)

    print(f"\n[1/4] 抓取教师数据 (ID 1 ~ {MAX_ID})...", flush=True)
    profs = fetch_all_profs()
    print(f"  共获取 {len(profs)} 位教师数据", flush=True)

    print(f"\n[2/4] 抓取标签数据...", flush=True)
    tags = fetch_tags()
    print(f"  共获取 {len(tags)} 个标签", flush=True)

    print(f"\n[3/4] 抓取评价数据...", flush=True)
    reviews_by_prof = fetch_all_reviews(profs)

    fetch_time = datetime.now().strftime("%Y-%m-%d %H:%M")

    print(f"\n[4/4] 保存数据并生成页面...", flush=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump({"profs": profs, "tags": tags, "reviews": reviews_by_prof, "fetchTime": fetch_time}, f, ensure_ascii=False, indent=2)

    generate_html(profs, tags, reviews_by_prof, fetch_time)

    total_reviews = sum(len(v) for v in reviews_by_prof.values())
    print(f"\n{'=' * 50}", flush=True)
    print(f"完成！", flush=True)
    print(f"  教师总数: {len(profs)}", flush=True)
    print(f"  评价总数: {total_reviews}", flush=True)
    print(f"  更新时间: {fetch_time}", flush=True)
    print(f"  HTML 文件: {HTML_FILE}", flush=True)
    print(f"  数据文件: {DATA_FILE}", flush=True)
    print(f"{'=' * 50}", flush=True)


if __name__ == "__main__":
    main()
