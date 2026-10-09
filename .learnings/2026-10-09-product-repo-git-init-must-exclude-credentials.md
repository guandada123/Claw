# ★升级候选：产物型 git 仓库初始化时，`.gitignore` 必须显式排除凭据（P1·系统性）

**日期**：2026-10-09（给技能库建独立 git 版本控制时**自己引入**的缺口）
**等级**：P1（凭据进入版本控制且**全程无任何告警**；泄漏只发生在 clone/push/bundle 之后）
**类型**：engineering_practice（可复用 SOP + 机器守卫）
**状态**：已修复并复验；守卫已落地

---

## 一、事故

给 `~/.workbuddy/skills` 建独立 git 基线时，`.gitignore` 只写了三类忽略：
构建/缓存（`__pycache__`、`.venv`…）、备份（`*.bak`）、运行产物（`*.log`、`_trash/`）。

**漏了凭据** → 基线快照 `a5dfedb` 把 `.neodata_token`
（`{"token": "<35B>", "saved_at": 1783349728}`）连同 blob `df43070` 一并纳入版本控制。

危险点不在于「文件被提交」这件事本身 —— 而在于：

1. **它不报错**：`git commit` 成功、`git status` 是 clean、`skills_vcs.sh status` 也报 clean。
2. **没有任何检查会看见它**：仓库健康检查看的是「工作区是否干净」，不是「对象库里有什么」。
3. **真正的泄漏发生在很久以后**：某天为了备份把这个仓库 push 到远端 / bundle 给别处，
   凭据才随历史一起出去 —— 那时已很难溯源。

属「建立版本控制」这个动作**自身引入的次级缺口**。

## 二、为什么「把文件从索引移除」不等于修好

修的第一步是常规操作：补 `.gitignore` + `git rm --cached .neodata_token`（保留盘上文件）。
做完之后：

```
$ git ls-files | grep token      # 空 —— 索引里确实没了
$ git log --oneline -- .neodata_token
a5dfedb chore(skills): 首次纳入版本控制 —— 技能库基线快照   # ← 仍在历史里
```

`git rm --cached` 只是让**未来**不再跟踪；**过去**的提交里那份仍在对象库里，
`git show a5dfedb:.neodata_token` 照样读得出来。

所以检查必须落在**对象库**上，而不是索引上：

```
$ git rev-list --objects --all | grep -i neodata_token
df4307048e8d9c6b11eb877c7e1da786f3fa7b7c .neodata_token   # ← 这才是真相
```

本次按「无 remote 可安全重写」用 `filter-branch` 洗全历史 + `gc --prune=now`，
并用 `git cat-file -e <旧blob>` 反证旧对象确已不可达。

## 三、固化：三层守卫 + 一个脚本

新增 `.workbuddy/scripts/scan_repo_credentials.py`（带 7 项离线测试），对每个仓库查三件事：

| 层 | 查什么 | 为什么单独列 |
|---|---|---|
| L1 索引 | `git ls-files` 里的凭据类文件 | 常规检查只做到这里 |
| **L2 对象库** | `git rev-list --objects --all` 里的凭据路径 | ★ 唯一能发现「已移出索引但仍在历史」的层 |
| L3 `.gitignore` 覆盖 | 凭据模式是否已被显式忽略 | 防复发（不看内容，只看覆盖） |

判据排除模板/文档后缀（`.example` / `.sample` / `.template` / `.md` / `.txt`），避免误报。

**反证已做**：临时仓库里复现「先提交 → 再 `rm --cached`」两步，脚本同时报出
`L1-index .env` 与 `L2-objectdb .neodata_token` —— 证明它抓的确实是 L2 那个盲区，
不是靠 L1 顺带命中。

## 四、规则候选（供周度升铁律）

- ★ **产物型 git 仓库（技能库 / 数据目录 / 生成物）初始化时，`.gitignore` 必须显式列出凭据模式**；
  默认忽略集里只写构建/缓存/备份 = 缺一半。
- ★ **「从索引移除」不等于修好**：验证凭据是否泄漏必须查**对象库**
  （`git rev-list --objects --all`），不能只查 `git ls-files`。
- ★ **洗历史前先确认无 remote**：有远端时 `filter-branch` 会改写公共历史，
  须走「先轮换凭据 → 再重写 → 再强推」的顺序；无远端才是纯本地可逆操作。
- ★ **重写历史会让所有引用该 sha 的文档失效**（本次基线 `a5dfedb` → `7fc1920`）：
  洗完后必须全局 `grep` 旧 sha 并同步更正（registry / CHANGELOG / 日志），
  否则留下的是「看起来能回滚、实际指不到」的假回滚点。

## 五、复查点

- 下次给任何目录建 git 仓库时：建完**立刻**跑一次 `scan_repo_credentials.py <repo>`，rc 必须为 0。
- 该守卫的默认扫描面目前是 `~/.workbuddy/skills`；若新建产物型仓库，把它加进默认列表或显式传参。
- 本条的 L2 检查依赖 `git rev-list --objects --all`，只覆盖「当前所有 ref 可达的对象」；
  已被 `gc` 回收的不可达对象不在扫描面内（属预期，那部分已不可复原读取）。
