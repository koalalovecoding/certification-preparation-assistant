# Agents League Hackathon — Reasoning Agents Track
## 项目笔记与关键信息汇总

---

## 基本信息

- **活动名称**：Agents League Hackathon
- **时间**：June 4–14, 2026
- **轨道**：Reasoning Agents（Battle #2）
- **截止日期**：June 14, 2026（具体时间需在官网或 Discord 确认）
- **项目名称**：tat-reasoning-agent
- **项目路径**：`~/Desktop/PythonProject1/tat-reasoning-agent`

---

## 必须满足的要求

1. 实现多 agent 系统，对应挑战场景
2. 使用 Microsoft Foundry（UI 或 SDK）和/或 Microsoft Agent Framework
3. 展示跨 agent 的推理和多步决策
4. 集成至少一个 Microsoft IQ 层（三个都用更好）
5. 只使用合成数据，不含真实 PII
6. 系统可演示，有 demo 视频
7. GitHub repo 公开，包含 README
8. README 中注明数据集为合成数据，仅用于演示

---

## 评分标准

| 维度 | 权重 |
|------|------|
| Accuracy & Relevance | 25% |
| Reasoning & Multi-step Thinking | 25% |
| Reliability & Safety | 20% |
| Creativity & Originality | 15% |
| User Experience & Presentation | 15% |

### 加分项（Highly Valued Extras）
- Evaluations、telemetry、observability
- 高级推理模式
- Responsible AI 控制和 fallback
- 清晰的 hosted deployment 方案

---

## 任务场景

构建一个**企业内部认证备考管理系统**，帮助组织管理员工的技术认证考试流程。

系统需要能够：
- 理解认证要求与组织角色的对应关系
- 生成团队级别和角色级别的学习计划
- 从经批准的知识源提供有来源引用的练习题
- 提供团队和个人进度反馈
- 根据真实工作上下文和团队容量调整学习计划
- 呈现经理级别的团队备考状态洞察

---

## 架构设计

### 整体结构：Hub and Spoke

```
Input Guard → Dispatcher（主 agent / Hub）
                    ↓
    ┌───────────────┼───────────────┐
    ↓               ↓               ↓
Learning Path   Study Plan     Engagement
Curator         Generator       Agent
    └───────────────┼───────────────┘
                    ↓
            Human in the Loop
            （学员确认是否准备好）
                    ↓
            Assessment Agent
                    ↓
        通过 → 推荐下一认证
        未通过 → 循环回 Study Plan Generator
                    ↓
            Manager Insights Agent（独立，经理随时可查）
```

### 关键设计说明
- **Dispatcher** 即 Coordinator/Orchestrator/主 agent，负责接收输入、分发任务、控制流程
- **Input Guard** 是在 Dispatcher 之前额外添加的过滤 agent，过滤与考试无关的输入，体现 Responsible AI 设计
- **Hub and Spoke 结构**：子 agent 只和 Dispatcher 通信，不互相直接通信
- **循环机制**：Assessment 未通过时触发重新规划，这是系统体现 reasoning 的核心
- **Human in the Loop**：学员自己决定是否准备好接受评估

---

## 五个子 Agent 详细说明

### 1. Input Guard Agent（自定义新增）
- **职责**：过滤与认证考试无关的输入，不相关的直接拒绝
- **好处**：防止资源浪费，提升系统健壮性，满足 Reliability & Safety 评分项

### 2. Learning Path Curator Agent
- **职责**：根据员工角色和目标认证，推荐相关学习内容
- **输入**：员工角色 + 目标认证（如 Cloud Engineer + AZ-204）
- **输出**：有引用来源的学习资源列表
- **使用 IQ**：
  - **Foundry IQ**（主要）：从知识库检索认证相关资料，返回带 citation 的内容
  - 可选接入 Microsoft Learn MCP server 获取微软官方文档
- **关键要求**：返回 cited content，不能是无来源的自由文本

### 3. Study Plan Generator Agent
- **职责**：将学习内容转化为具体可执行的时间表
- **输入**：Learning Path Curator 的输出 + 员工工作负载数据
- **输出**：按周/天排列的学习计划
- **使用 IQ**：
  - **Fabric IQ**（主要）：提供认证所需学时、技能模块、先修关系等结构化数据
  - **Work IQ**（扩展）：官方架构未要求，但加入可让计划更现实（知道员工每周实际空闲时间）
- **功能扩展想法**：
  - 进度条显示学习完成百分比
  - 学习速度预测（根据合成历史数据预测需要多少天）

### 4. Engagement Agent
- **职责**：追踪进度，根据员工工作节奏发送提醒
- **输入**：Study Plan + Work IQ 工作信号数据
- **使用 IQ**：
  - **Work IQ**（主要）：读取会议负载、专注时间、偏好学习时段
- **关键行为**：
  - 避开会议密集时段
  - 根据个人工作节奏个性化提醒
  - 不使用一刀切的提醒逻辑

### 5. Assessment Agent
- **职责**：评估学员备考是否达标
- **使用 IQ**：
  - **Foundry IQ**：从知识库生成有引用来源的题目
  - **Fabric IQ**：提供通过分数线、技能模块权重等业务规则
- **功能要求**：
  - 题目覆盖认证的所有技能模块（不单一方向）
  - 按模块分配出题比例
  - 评分后告知学员哪个模块最薄弱（错题分析）
  - 自适应难度：答对出难题，答错出简单题
  - 把结果传回规划循环，同时传给 Manager Insights Agent
- **注意**：不使用 LLM 自报 confidence score，改用可验证信号（检索相关度、历史通过率、进度 vs 剩余时间）

### 6. Manager Insights Agent
- **职责**：提供团队级别的备考状态可视化
- **使用 IQ**：
  - **Work IQ**：团队工作负载和容量信号
  - **Fabric IQ**：技能缺口、通过率、劳动力准备情况的语义分析
- **输出内容**：
  - 按团队/角色/认证轨道汇总学习进度
  - 风险预警（距考试不足 X 天但进度不足 Y%）
  - 团队对比（不同角色通过率对比）
  - 不暴露个人敏感数据，只呈现聚合数据

---

## Microsoft IQ 三层说明

### Foundry IQ
- **本质**：可配置的多源知识库
- **作用**：从上传的文档中检索答案，返回带 citation 的回答
- **支持的数据源**：Azure Blob Storage、SharePoint、OneLake、公开网页
- **底层技术**：Azure AI Search
- **用于**：Learning Path Curator、Assessment Agent
- **一句话**：给 agent 提供有引用来源的私有知识库

### Work IQ
- **本质**：Microsoft 365 的工作上下文智能层
- **作用**：提供员工会议负载、专注时间、工作节奏等信号
- **在本项目中**：用合成数据模拟（不需要真实 Microsoft 365 账号）
- **用于**：Engagement Agent、Manager Insights Agent
- **一句话**：提供员工工作上下文信号，让 agent 能做出符合实际安排的决策

### Fabric IQ
- **本质**：Microsoft Fabric 的语义层，核心是 Ontology（本体论）
- **作用**：把业务概念（角色、认证、技能、通过标准）之间的关系结构化
- **用于**：Study Plan Generator、Assessment Agent、Manager Insights Agent
- **一句话**：把业务概念之间的关系结构化，让 agent 能基于这些关系进行推理

---

## 端到端工作流

1. 学员输入想学的主题
2. **Input Guard** 过滤无关输入
3. **Dispatcher** 接收并分发任务
4. **Foundry IQ** 从知识库检索学习资料
5. **Fabric IQ** 解析角色-认证-技能结构化数据
6. **Work IQ** 识别员工的现实学习时间窗口
7. **Learning Path Curator** 生成有引用的学习路径
8. **Study Plan Generator** 生成考虑工作负载的学习计划
9. **Engagement Agent** 根据工作节奏保持学员跟进
10. 学员确认准备好后，触发 **Assessment Agent**
11. Assessment 通过 → 推荐下一认证；未通过 → 循环回第8步
12. **Manager Insights Agent** 随时提供团队级别洞察

---

## 合成数据

### Learner Performance
```json
[
  {"learner_id": "L-1001", "role": "Cloud Engineer", "certification": "AZ-204", "practice_score_avg": 67, "hours_studied": 18, "exam_outcome": "Fail"},
  {"learner_id": "L-1002", "role": "DevOps Engineer", "certification": "AZ-400", "practice_score_avg": 82, "hours_studied": 24, "exam_outcome": "Pass"},
  {"learner_id": "L-1003", "role": "Data Engineer", "certification": "DP-203", "practice_score_avg": 74, "hours_studied": 20, "exam_outcome": "Pass"}
]
```

### Work Activity Signals
```json
[
  {"employee_id": "EMP-001", "meeting_hours_per_week": 22, "focus_hours_per_week": 10, "preferred_learning_slot": "Morning"},
  {"employee_id": "EMP-002", "meeting_hours_per_week": 15, "focus_hours_per_week": 18, "preferred_learning_slot": "Afternoon"}
]
```

### Fabric IQ Semantic Model Seed
```json
{
  "certifications": [
    {"id": "AZ-204", "skills": ["API Development", "Azure Functions", "Storage"], "recommended_hours": 20},
    {"id": "AZ-400", "skills": ["CI/CD", "Monitoring", "GitHub Actions"], "recommended_hours": 25}
  ]
}
```

### 扩展方向
- Learner Performance：增加更多角色（Data Scientist/DP-100，Security Engineer/AZ-500）
- Work Activity Signals：覆盖更多场景（会议极多/专注时间充裕）
- Fabric IQ：加入先修关系（AZ-305 需先通过 AZ-204），加入通过分数线
- 每类数据 5–10 条足够 demo 使用

---

## 加分功能列表

| 功能 | 对应评分维度 |
|------|-------------|
| Input Guard Agent | Reliability & Safety 20% |
| 可视化监控（Foundry telemetry） | Highly Valued Extra |
| 评估模块（test cases + scoring） | Highly Valued Extra |
| 自适应出题难度 | Reasoning 25% |
| 错题模块分析 | Accuracy 25% |
| 风险预警（Manager Agent） | Reasoning 25% |
| 进度条（Study Plan） | UX 15% |
| Study Plan 同时使用 Work IQ | Reasoning 25% |
| Hosted deployment | Highly Valued Extra |

---

## 环境配置状态

- [x] Git 已安装（2.54.0，Xcode Command Line Tools）
- [x] Python 虚拟环境已创建并激活（`.venv`）
- [x] 项目目录已初始化（`tat-reasoning-agent`）
- [x] `.gitignore` 已配置（排除 `.venv/` 和 `.env`）
- [x] Azure 账号已注册（$200 credit，30天有效）
- [x] Microsoft Foundry Portal 已访问（ai.azure.com）
- [x] Project endpoint 已获取
- [x] `.env` 文件已填写
- [x] VS Code 已安装
- [x] Discord 已加入

---

## 安全注意事项

- 绝不提交 `.env` 文件到 GitHub
- 绝不在代码中硬写 API key 或密钥
- 所有数据使用合成数据，明显虚构的标识符（L-1001、EMP-001、TEAM-A）
- README 中注明数据为合成数据

---

## 有用链接

- Hackathon 页面：Agents League
- Foundry Portal：https://ai.azure.com
- Azure Portal：https://portal.azure.com
- Microsoft Learn MCP server：https://github.com/microsoftdocs/mcp
- Foundry IQ 文档：https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/what-is-foundry-iq
- Work IQ 文档：https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/work-iq
- Fabric IQ 博客：https://blog.fabric.microsoft.com/en-in/blog/introducing-fabric-iq-the-semantic-foundation-for-enterprise-ai
- Discord：https://aka.ms/agentsleague/discord
