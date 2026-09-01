#!/usr/bin/env python3
"""Build research topic benchmark cases across diverse AI sub-fields.

Each topic = research sub-field with background, open challenges, and references.
The Agent writes the proposal. The AI Judge scores it.
"""
import json, os, time
from pathlib import Path
from openai import OpenAI

client = OpenAI(
    api_key=os.environ.get("AI4PROPOSAL_API_KEY", ""),
    base_url=os.environ.get("AI4PROPOSAL_BASE_URL", "https://api.openai.com/v1"),
    timeout=180,
)

# ═══ Topic seeds across 20+ domains, 100 seeds ═══
TOPIC_SEEDS = [
    # ── AI Safety & Alignment (5) ──
    {"domain":"ai_safety","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":3000000,"currency":"CNY","dur":36},"seed":"多模态大模型内容安全对齐：跨模态越狱攻击防御、3H平衡优化、安全对齐数据构建"},
    {"domain":"ai_safety","lang":"en","sponsor":"NSF","budget":{"amount":800000,"currency":"USD","dur":36},"seed":"Robust AI alignment under distribution shift: OOD generalization of safety constraints, adversarial robustness of RLHF models, compositional safety guarantees"},
    {"domain":"ai_safety","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":2800000,"currency":"CNY","dur":36},"seed":"大模型红队测试与自动化安全评估：对抗性提示生成、多维度安全评测基准、自动化红队攻防框架"},
    {"domain":"ai_safety","lang":"en","sponsor":"NSF","budget":{"amount":750000,"currency":"USD","dur":36},"seed":"Mechanistic interpretability for AI safety: sparse autoencoders for feature discovery, circuit analysis for safety-critical behaviors, causal intervention methods"},
    {"domain":"ai_safety","lang":"zh","sponsor":"科技部重点研发","budget":{"amount":5000000,"currency":"CNY","dur":48},"seed":"AI系统价值对齐与宪法AI：多维价值观冲突消解、文化适应性对齐、人机协作式价值学习"},

    # ── Agentic AI (5) ──
    {"domain":"agentic_ai","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":2800000,"currency":"CNY","dur":36},"seed":"基于LLM的自主科学发现Agent：实验设计自动化、假设生成与验证、多步推理中的错误累积抑制"},
    {"domain":"agentic_ai","lang":"en","sponsor":"NSF","budget":{"amount":850000,"currency":"USD","dur":36},"seed":"Multi-agent coordination for complex task solving: decentralized planning, emergent communication protocols, robust task decomposition under partial observability"},
    {"domain":"agentic_ai","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":2600000,"currency":"CNY","dur":36},"seed":"代码Agent的可靠性与安全：自主代码生成与验证、沙盒安全执行、长程编程任务中的上下文管理"},
    {"domain":"agentic_ai","lang":"en","sponsor":"NSF","budget":{"amount":780000,"currency":"USD","dur":36},"seed":"Tool-augmented language agents: API learning from documentation, compositional tool use, failure recovery in open-ended tool environments"},
    {"domain":"agentic_ai","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":2700000,"currency":"CNY","dur":36},"seed":"GUI Agent的视觉理解与操作：跨应用界面泛化、多步界面操作规划、视觉-操作对齐学习"},

    # ── Multimodal Learning (5) ──
    {"domain":"multimodal","lang":"zh","sponsor":"国家重点研发计划","budget":{"amount":5000000,"currency":"CNY","dur":48},"seed":"多模态大模型细粒度理解：跨模态视觉-语言-音频联合对齐、长视频时序推理、多轮视觉对话"},
    {"domain":"multimodal","lang":"en","sponsor":"NSF","budget":{"amount":900000,"currency":"USD","dur":36},"seed":"Grounded multimodal reasoning: neuro-symbolic approaches to spatial-temporal understanding, compositional visual reasoning, vision-language-action models"},
    {"domain":"multimodal","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":2500000,"currency":"CNY","dur":36},"seed":"医学多模态基础模型：CT/MRI/病理/基因多模态融合、跨模态医学知识对齐、少样本临床决策支持"},
    {"domain":"multimodal","lang":"en","sponsor":"NSF","budget":{"amount":820000,"currency":"USD","dur":36},"seed":"Audio-visual scene understanding: sound source localization, audio-visual event parsing, multimodal navigation for embodied agents"},
    {"domain":"multimodal","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":2400000,"currency":"CNY","dur":36},"seed":"视频理解与生成统一模型：视频-文本双向生成、物理世界动力学理解、长视频一致性生成"},

    # ── Efficient ML (5) ──
    {"domain":"efficient_ml","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":2600000,"currency":"CNY","dur":36},"seed":"大模型端侧部署：混合精度量化、结构化剪枝、知识蒸馏在极端压缩率下的精度保持"},
    {"domain":"efficient_ml","lang":"en","sponsor":"NSF","budget":{"amount":650000,"currency":"USD","dur":36},"seed":"Energy-efficient training paradigms: beyond mixed precision and gradient accumulation, fundamentally more efficient optimization algorithms"},
    {"domain":"efficient_ml","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":2500000,"currency":"CNY","dur":36},"seed":"稀疏化大模型推理：动态稀疏激活、MoE路由优化、稀疏注意力机制的硬件友好设计"},
    {"domain":"efficient_ml","lang":"en","sponsor":"NSF","budget":{"amount":700000,"currency":"USD","dur":36},"seed":"TinyML for ubiquitous computing: sub-milliwatt neural architectures, on-device continual learning, sensor-adaptive model compression"},
    {"domain":"efficient_ml","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":2300000,"currency":"CNY","dur":36},"seed":"绿色AI：训练与推理碳排放精确建模、碳感知模型调度、面向碳中和的模型全生命周期优化"},

    # ── AI for Science (6) ──
    {"domain":"ai4science","lang":"zh","sponsor":"国家重点研发计划","budget":{"amount":6000000,"currency":"CNY","dur":48},"seed":"AI蛋白质设计：功能预测、动态构象采样、固有无序蛋白与多域蛋白的智能设计"},
    {"domain":"ai4science","lang":"en","sponsor":"NSF","budget":{"amount":1200000,"currency":"USD","dur":48},"seed":"Universal ML interatomic potentials: reactive chemistry, excited states, generalizing across the periodic table with physics-informed neural networks"},
    {"domain":"ai4science","lang":"zh","sponsor":"国家重点研发计划","budget":{"amount":5500000,"currency":"CNY","dur":48},"seed":"AI天气与气候预测：物理约束深度学习、多尺度时空预测、极端事件预警与归因"},
    {"domain":"ai4science","lang":"en","sponsor":"NSF","budget":{"amount":1100000,"currency":"USD","dur":48},"seed":"AI-accelerated materials discovery: active learning for DFT, generative models for crystal structure prediction, multi-objective materials optimization"},
    {"domain":"ai4science","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":3200000,"currency":"CNY","dur":36},"seed":"AI辅助药物发现：分子生成与优化、ADMET预测、基于结构的虚拟筛选与分子动力学模拟"},
    {"domain":"ai4science","lang":"en","sponsor":"NSF","budget":{"amount":950000,"currency":"USD","dur":36},"seed":"Neural PDE solvers: learned operators for fluid dynamics, multi-scale physics simulation, hybrid physics-ML for turbulence modeling"},

    # ── Robotics (5) ──
    {"domain":"robotics","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":3200000,"currency":"CNY","dur":36},"seed":"通用机器人操作技能：跨任务泛化、非结构化环境适应、Sim-to-Real视觉与动力学鸿沟消除"},
    {"domain":"robotics","lang":"en","sponsor":"NSF","budget":{"amount":900000,"currency":"USD","dur":36},"seed":"Visuo-tactile dexterous manipulation: multi-fingered hands for deformable objects, tactile-based in-hand pose estimation, learning from human demonstration"},
    {"domain":"robotics","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":3000000,"currency":"CNY","dur":36},"seed":"人形机器人全身运动控制：动态平衡、全身协调操作、非结构化地形下的鲁棒步态规划"},
    {"domain":"robotics","lang":"en","sponsor":"NSF","budget":{"amount":880000,"currency":"USD","dur":36},"seed":"Foundation models for robot learning: large-scale robot data collection, cross-embodiment transfer, language-conditioned visuomotor policies"},
    {"domain":"robotics","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":2800000,"currency":"CNY","dur":36},"seed":"多机器人协同：分布式感知与决策、通信约束下的协同控制、异构机器人团队任务分配"},

    # ── Privacy & Security (5) ──
    {"domain":"privacy_security","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":2400000,"currency":"CNY","dur":36},"seed":"大模型隐私保护：训练数据记忆与机器遗忘、成员推理攻击防御、联邦学习中的模型逆向防护"},
    {"domain":"privacy_security","lang":"en","sponsor":"NSF","budget":{"amount":700000,"currency":"USD","dur":36},"seed":"Differential privacy for foundation models: DP-SGD at scale, privacy-utility Pareto optimization, auditing and certification of privacy guarantees"},
    {"domain":"privacy_security","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":2500000,"currency":"CNY","dur":36},"seed":"大模型供应链安全：模型水印与版权保护、后门攻击检测与防御、安全模型分发与更新"},
    {"domain":"privacy_security","lang":"en","sponsor":"NSF","budget":{"amount":680000,"currency":"USD","dur":36},"seed":"Adversarial robustness of vision-language models: multimodal adversarial attacks, certified robustness, defense through multimodal inconsistency detection"},
    {"domain":"privacy_security","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":2200000,"currency":"CNY","dur":36},"seed":"生成式AI的深度伪造检测：AIGC内容溯源、多模态深度伪造鉴别、面向社交网络的传播检测"},

    # ── NLP (5) ──
    {"domain":"nlp","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":2700000,"currency":"CNY","dur":36},"seed":"大模型幻觉机制与抑制：事实性错误溯源、检索增强生成的幻觉控制、长文本中的自一致性保持"},
    {"domain":"nlp","lang":"en","sponsor":"NSF","budget":{"amount":780000,"currency":"USD","dur":36},"seed":"Efficient long-context language modeling: sub-quadratic attention mechanisms, length extrapolation, memory-augmented architectures for million-token contexts"},
    {"domain":"nlp","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":2600000,"currency":"CNY","dur":36},"seed":"多语言大模型的公平性：低资源语言性能均衡、跨语言知识迁移、文化适应性语言生成"},
    {"domain":"nlp","lang":"en","sponsor":"NSF","budget":{"amount":720000,"currency":"USD","dur":36},"seed":"Faithful reasoning in language models: chain-of-thought faithfulness, reasoning verification, neuro-symbolic integration for provable reasoning"},
    {"domain":"nlp","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":2500000,"currency":"CNY","dur":36},"seed":"面向专业领域的大模型适配：法律/医疗/金融领域知识注入、专业术语理解、领域风险可控生成"},

    # ── Computer Vision (5) ──
    {"domain":"vision","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":2500000,"currency":"CNY","dur":36},"seed":"开放世界视觉理解：未知类别检测、增量学习中的灾难性遗忘抑制、分布外泛化"},
    {"domain":"vision","lang":"en","sponsor":"NSF","budget":{"amount":750000,"currency":"USD","dur":36},"seed":"Prior-driven 3D reconstruction from sparse views: single-image to 3D, feed-forward Gaussian Splatting, open-vocabulary 3D scene understanding"},
    {"domain":"vision","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":2400000,"currency":"CNY","dur":36},"seed":"以视觉为中心的自动驾驶感知：BEV感知、端到端视觉驾驶、多传感器融合与鲁棒性"},
    {"domain":"vision","lang":"en","sponsor":"NSF","budget":{"amount":800000,"currency":"USD","dur":36},"seed":"Egocentric video understanding: first-person action recognition, episodic memory from video, hand-object interaction modeling"},
    {"domain":"vision","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":2300000,"currency":"CNY","dur":36},"seed":"遥感图像智能解译：高分辨率遥感目标检测、多时相变化检测、多模态遥感数据融合"},

    # ── AI Infrastructure (5) ──
    {"domain":"ai_infra","lang":"zh","sponsor":"国家重点研发计划","budget":{"amount":8000000,"currency":"CNY","dur":48},"seed":"面向大模型的分布式训练系统：通信-计算重叠、异构硬件自适应调度、弹性容错训练"},
    {"domain":"ai_infra","lang":"en","sponsor":"NSF","budget":{"amount":1000000,"currency":"USD","dur":36},"seed":"Disaggregated LLM inference: prefill-decode disaggregation, adaptive KV-cache management, heterogeneous GPU orchestration"},
    {"domain":"ai_infra","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":2800000,"currency":"CNY","dur":36},"seed":"大模型推理服务系统：动态批处理、预测式KV缓存、SLA保障下的资源自适应调度"},
    {"domain":"ai_infra","lang":"en","sponsor":"NSF","budget":{"amount":850000,"currency":"USD","dur":36},"seed":"Memory-efficient training of trillion-parameter models: ZeRO-style parallelism improvements, activation checkpointing optimal strategies, heterogeneous memory hierarchies"},
    {"domain":"ai_infra","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":2600000,"currency":"CNY","dur":36},"seed":"面向AI的存算一体芯片架构：近存计算、模拟存算、数字存算混合架构的编程模型与编译优化"},

    # ── Embodied AI (4) ──
    {"domain":"embodied_ai","lang":"en","sponsor":"NSF","budget":{"amount":920000,"currency":"USD","dur":36},"seed":"Embodied navigation with semantic mapping: open-vocabulary object navigation, hierarchical exploration, long-horizon instruction following in unseen environments"},
    {"domain":"embodied_ai","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":3100000,"currency":"CNY","dur":36},"seed":"具身智能的物理交互学习：接触式操作策略学习、力觉-视觉融合感知、物理推理驱动的操作规划"},
    {"domain":"embodied_ai","lang":"en","sponsor":"NSF","budget":{"amount":860000,"currency":"USD","dur":36},"seed":"World models for embodied agents: learning predictive models of environment dynamics, planning in learned latent spaces, sim-to-real world model transfer"},
    {"domain":"embodied_ai","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":2900000,"currency":"CNY","dur":36},"seed":"灵巧手操作中的触觉感知与技能学习：高分辨率触觉传感器信号处理、触觉-视觉融合操作表征、精细操作技能迁移"},

    # ── Healthcare AI (5) ──
    {"domain":"healthcare_ai","lang":"zh","sponsor":"国家重点研发计划","budget":{"amount":4500000,"currency":"CNY","dur":48},"seed":"AI辅助精准医疗：多组学数据整合、个性化治疗方案推荐、临床试验结果预测与患者分层"},
    {"domain":"healthcare_ai","lang":"en","sponsor":"NSF","budget":{"amount":950000,"currency":"USD","dur":36},"seed":"Federated learning for healthcare: cross-institution model training with privacy guarantees, non-IID clinical data, robust aggregation against poisoned clients"},
    {"domain":"healthcare_ai","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":2700000,"currency":"CNY","dur":36},"seed":"医学影像基础模型：跨器官跨模态通用影像理解、医学视觉-语言对齐、放射报告自动生成与质控"},
    {"domain":"healthcare_ai","lang":"en","sponsor":"NSF","budget":{"amount":880000,"currency":"USD","dur":36},"seed":"AI for drug repurposing: knowledge graph reasoning over biomedical literature, transcriptome-based drug-disease matching, clinical evidence synthesis"},
    {"domain":"healthcare_ai","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":2500000,"currency":"CNY","dur":36},"seed":"可穿戴设备健康监测AI：多传感器时序信号融合、个性化健康基线建模、早期疾病预警"},

    # ── ML Theory (4) ──
    {"domain":"ml_theory","lang":"en","sponsor":"NSF","budget":{"amount":600000,"currency":"USD","dur":36},"seed":"Understanding in-context learning: theoretical frameworks for why transformers implement gradient descent during inference, optimal prompt design, emergence of reasoning"},
    {"domain":"ml_theory","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":2200000,"currency":"CNY","dur":36},"seed":"深度学习泛化理论：过参数化模型的隐式正则化、双下降现象的机理、sharpness-aware minimization理论"},
    {"domain":"ml_theory","lang":"en","sponsor":"NSF","budget":{"amount":580000,"currency":"USD","dur":36},"seed":"Scaling laws for neural networks: theoretical foundations of compute-optimal scaling, multi-modal scaling laws, predicting emergent capabilities"},
    {"domain":"ml_theory","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":2100000,"currency":"CNY","dur":36},"seed":"大模型表征空间的几何与拓扑分析：流形学习视角下的表征结构、线性表征假设的验证与突破"},

    # ── Climate & Sustainability AI (4) ──
    {"domain":"climate_ai","lang":"en","sponsor":"NSF","budget":{"amount":850000,"currency":"USD","dur":36},"seed":"Foundation models for Earth system science: multi-modal satellite data fusion, physics-informed weather prediction, carbon cycle modeling with ML"},
    {"domain":"climate_ai","lang":"zh","sponsor":"国家重点研发计划","budget":{"amount":5000000,"currency":"CNY","dur":48},"seed":"面向碳中和的AI优化：工业过程智能减碳、新能源功率预测、碳汇遥感估算与碳交易智能决策"},
    {"domain":"climate_ai","lang":"en","sponsor":"NSF","budget":{"amount":780000,"currency":"USD","dur":36},"seed":"AI for biodiversity monitoring: passive acoustic monitoring, camera trap image analysis, species distribution modeling under climate change"},
    {"domain":"climate_ai","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":2600000,"currency":"CNY","dur":36},"seed":"城市气候数字孪生：多源异构城市数据融合、微气候AI预测、极端天气城市韧性评估"},

    # ── AI for Education (3) ──
    {"domain":"ai_education","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":2000000,"currency":"CNY","dur":36},"seed":"大模型驱动的个性化教育：学习路径动态规划、知识追踪与认知诊断、自适应题目生成与自动批改"},
    {"domain":"ai_education","lang":"en","sponsor":"NSF","budget":{"amount":550000,"currency":"USD","dur":36},"seed":"AI tutoring systems with pedagogical reasoning: multi-turn Socratic dialogue, misconception detection and remediation, culturally responsive tutoring strategies"},
    {"domain":"ai_education","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":1800000,"currency":"CNY","dur":36},"seed":"编程教育中的智能代码反馈：程序错误自动诊断、代码风格与质量的智能评价、学习路径个性化推荐"},

    # ── AI for Code (3) ──
    {"domain":"ai4code","lang":"en","sponsor":"NSF","budget":{"amount":720000,"currency":"USD","dur":36},"seed":"Repository-scale code understanding: cross-file reasoning, API migration automation, test generation from specifications with coverage guarantees"},
    {"domain":"ai4code","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":2400000,"currency":"CNY","dur":36},"seed":"大模型辅助软件工程：需求到代码的端到端生成、代码审查自动化、遗留系统重构的AI辅助"},
    {"domain":"ai4code","lang":"en","sponsor":"NSF","budget":{"amount":680000,"currency":"USD","dur":36},"seed":"Provably correct code generation: integrating formal verification with LLM-based code synthesis, proof-carrying code, neuro-symbolic program synthesis"},

    # ── AI & Society (3) ──
    {"domain":"ai_society","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":2000000,"currency":"CNY","dur":36},"seed":"AI决策的公平性与可问责性：算法歧视的检测与缓解、可解释AI在公共决策中的应用、AI审计框架"},
    {"domain":"ai_society","lang":"en","sponsor":"NSF","budget":{"amount":600000,"currency":"USD","dur":36},"seed":"Democratic AI governance: participatory AI alignment, community-driven model evaluation, decentralized AI oversight mechanisms"},
    {"domain":"ai_society","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":2200000,"currency":"CNY","dur":36},"seed":"生成式AI对信息生态的影响：AI生成内容的传播动力学、人机协作信息验证、算法推荐对公共舆论的影响"},

    # ── AI Hardware (3) ──
    {"domain":"ai_hardware","lang":"en","sponsor":"NSF","budget":{"amount":900000,"currency":"USD","dur":36},"seed":"Beyond-GPU AI accelerators: photonic computing for neural networks, analog in-memory computing architectures, neuromorphic chips for spiking neural networks"},
    {"domain":"ai_hardware","lang":"zh","sponsor":"国家重点研发计划","budget":{"amount":7000000,"currency":"CNY","dur":48},"seed":"自主可控AI芯片架构：面向大模型训练的领域专用架构、高带宽存储子系统、芯片间高速互联"},
    {"domain":"ai_hardware","lang":"en","sponsor":"NSF","budget":{"amount":820000,"currency":"USD","dur":36},"seed":"Co-design of model architectures and hardware: hardware-aware neural architecture search, quantization-aware training, sparsity-aware accelerator design"},

    # ── AI for Finance (2) ──
    {"domain":"ai_finance","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":2300000,"currency":"CNY","dur":36},"seed":"金融大模型的风险管理：市场异常检测、信用风险评估中的公平性、金融文本的可信生成"},
    {"domain":"ai_finance","lang":"en","sponsor":"NSF","budget":{"amount":620000,"currency":"USD","dur":36},"seed":"Robust financial ML under regime change: distribution shift in market dynamics, causal inference for policy impact, adversarial robustness in trading models"},

    # ── AI for Agriculture (2) ──
    {"domain":"ai_agriculture","lang":"zh","sponsor":"国家重点研发计划","budget":{"amount":3500000,"currency":"CNY","dur":48},"seed":"智慧农业AI：多源遥感作物监测与估产、病虫害智能识别与预警、精准农业机器人"},
    {"domain":"ai_agriculture","lang":"en","sponsor":"NSF","budget":{"amount":650000,"currency":"USD","dur":36},"seed":"AI for sustainable agriculture: precision irrigation with reinforcement learning, crop phenotyping from drone imagery, soil carbon monitoring"},

    # ── AI for Energy (2) ──
    {"domain":"ai_energy","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":2600000,"currency":"CNY","dur":36},"seed":"AI驱动的智能电网：分布式能源优化调度、电力负荷概率预测、电网故障的实时检测与自愈"},
    {"domain":"ai_energy","lang":"en","sponsor":"NSF","budget":{"amount":750000,"currency":"USD","dur":36},"seed":"AI for fusion energy: plasma control with deep RL, disruption prediction, surrogate models for turbulent transport, experimental design optimization"},

    # ── AI for Neuroscience (3) ──
    {"domain":"ai_neuroscience","lang":"en","sponsor":"NSF","budget":{"amount":800000,"currency":"USD","dur":36},"seed":"Brain-computer interfaces with AI: neural decoding with transformers, closed-loop neuromodulation, few-shot calibration for motor BCIs"},
    {"domain":"ai_neuroscience","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":2800000,"currency":"CNY","dur":36},"seed":"大规模神经信号基础模型：跨脑区跨物种的神经信号统一表征、神经元类型自动分类、神经活动预测"},
    {"domain":"ai_neuroscience","lang":"en","sponsor":"NSF","budget":{"amount":780000,"currency":"USD","dur":36},"seed":"Connectomics with AI: automated synapse detection in EM volumes, brain circuit reconstruction, comparative connectomics across species"},

    # ── AI for Math (2) ──
    {"domain":"ai4math","lang":"en","sponsor":"NSF","budget":{"amount":650000,"currency":"USD","dur":36},"seed":"AI for mathematical discovery: automated theorem proving with LLMs, conjecture generation, formal proof verification at scale"},
    {"domain":"ai4math","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":2000000,"currency":"CNY","dur":36},"seed":"AI辅助数学研究：形式化数学的自动推理、数学问题的自动求解与证明、AI发现新数学结构"},

    # ── AI for Networks (2) ──
    {"domain":"ai_networks","lang":"en","sponsor":"NSF","budget":{"amount":700000,"currency":"USD","dur":36},"seed":"AI-native wireless networks: learned physical layer, semantic communication, distributed RL for spectrum sharing, O-RAN intelligent controllers"},
    {"domain":"ai_networks","lang":"zh","sponsor":"国家自然科学基金","budget":{"amount":2400000,"currency":"CNY","dur":36},"seed":"面向6G的AI原生网络：智能无线资源调度、信道预测与波束管理、网络切片的智能编排"},
]

SYSTEM = """You are a senior research program director at a major funding agency. Given a brief research seed, create a comprehensive research topic description.

The output will be used as a PROMPT for an AI agent to write a full grant proposal. Write a detailed research background with specific technical challenges, cite fictional but plausible papers as references, and clearly articulate the open gaps.

Output as a JSON object:
{
  "title": "concise research topic title",
  "background": "2-3 paragraphs covering state-of-the-art, key existing work with [1][2] citations, and open gaps/challenges",
  "challenges": ["specific challenge 1", "specific challenge 2", "specific challenge 3"],
  "references": [{"title": "...", "venue": "...", "year": 2024-2026, "authors": "..."}, ...]
}

CRITICAL:
- Background should be 800-1500 characters
- Challenges should be specific and technical, not generic
- References should be 4-6 papers, venues should be real (NeurIPS, ICML, CVPR, ACL, Nature, Science, etc.)
- Write in the specified language
- Match the sponsor's domain focus
- Leave the SOLUTION open — the Agent will write the proposal to address these challenges"""


def generate_topic(seed: dict) -> dict:
    lang = seed.get("lang", seed.get("language", "en"))
    user = f"""Language: {'Chinese' if lang == 'zh' else 'English'}
Sponsor: {seed['sponsor']}
Domain: {seed['domain']}

Research seed: {seed['seed']}

Create a comprehensive research topic. Write in {lang}."""

    for attempt in range(3):
        try:
            r = client.chat.completions.create(
                model="claude-opus-4-7",
                messages=[
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": user},
                ],
                max_tokens=1000,
                temperature=0.8,
            )
            text = r.choices[0].message.content.strip()
            # Extract JSON
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(text[start:end])
                data["domain"] = seed["domain"]
                data["language"] = lang
                data["sponsor"] = seed["sponsor"]
                data["budget"] = seed["budget"]
                return data
        except Exception as e:
            print(f"    Retry {attempt+1}: {e}")
            time.sleep(2)
    return {}


def main():
    out = Path("legacy/research_topics")
    if out.exists():
        import shutil
        shutil.rmtree(out)
    out.mkdir(parents=True)

    print(f"Generating {len(TOPIC_SEEDS)} research topics...\n")

    topics = []
    for i, seed in enumerate(TOPIC_SEEDS):
        lang = seed.get("lang", seed.get("language", "en"))
        lang_flag = "CN" if lang == "zh" else "EN"
        print(f"  [{i+1:3d}/{len(TOPIC_SEEDS)}] {lang_flag} {seed['domain']:20s} ... ", end="", flush=True)
        data = generate_topic(seed)
        if data:
            topic_id = f"topic_{i+1:03d}"
            data["topic_id"] = topic_id
            data["team"] = {"pi_name": "", "institution": "", "members": []}
            fpath = out / f"{topic_id}.json"
            json.dump(data, fpath.open("w"), indent=2, ensure_ascii=False)
            topics.append(data)
            blen = len(data.get("background", ""))
            clen = len(data.get("challenges", []))
            print(f"ok bg={blen}c ch={clen}")
        else:
            print("FAIL")
        time.sleep(0.3)

    # Summary
    domains = {}
    langs = {"zh": 0, "en": 0}
    for t in topics:
        domains[t["domain"]] = domains.get(t["domain"], 0) + 1
        langs[t.get("language", t.get("lang", "en"))] = langs.get(t.get("language", t.get("lang", "en")), 0) + 1

    print(f"\n{'='*60}")
    print(f"Generated {len(topics)} research topics")
    print(f"Languages: CN={langs['zh']}, EN={langs['en']}")
    print(f"Domains: {json.dumps(domains, indent=2)}")
    print(f"Saved to: {out}")


if __name__ == "__main__":
    main()
