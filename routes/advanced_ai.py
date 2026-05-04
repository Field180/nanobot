"""
Advanced AI Routes (P0-1 extraction from server_final.py)

Experimental cognitive system endpoints: predictive analytics, adaptive learning,
meta-learning, cross-domain transfer, autonomous decision, creative thinking,
multi-agent collaboration, goal planning, emotion, reasoning, causal, lifelong
learning, NLU, creative problem solving, and system integration hub.
"""
import logging
import sys
from datetime import datetime

from fastapi import APIRouter, Request

from server_state import WORKSPACE

logger = logging.getLogger(__name__)

router = APIRouter(tags=["advanced-ai"])

# ========== 预测性分析系统 API ==========

@router.post("/api/predictive/analyze")
async def predictive_analyze():
    """执行预测性分析"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from predictive_analytics import PredictiveEngine
        
        engine = PredictiveEngine(WORKSPACE)
        predictions = engine.generate_predictions()
        
        return {
            "success": True,
            "predictions_count": len(predictions),
            "predictions": [
                {
                    "type": p.prediction_type,
                    "description": p.description,
                    "probability": p.probability,
                    "expected_time": p.expected_time.isoformat(),
                    "severity": p.severity,
                    "recommended_action": p.recommended_action,
                    "confidence": p.confidence
                }
                for p in predictions
            ],
            "generated_at": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"预测分析失败: {e}")
        return {"success": False, "error": str(e)}

@router.post("/api/predictive/record")
async def record_metrics():
    """记录当前系统指标"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from predictive_analytics import PredictiveEngine
        
        engine = PredictiveEngine(WORKSPACE)
        engine.record_metrics()
        
        return {
            "success": True,
            "message": "已记录当前系统指标",
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"记录指标失败: {e}")
        return {"success": False, "error": str(e)}

@router.get("/api/predictive/summary")
async def predictive_summary(hours: int = 24):
    """获取预测摘要"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from predictive_analytics import PredictiveEngine
        
        engine = PredictiveEngine(WORKSPACE)
        summary = engine.get_prediction_summary(hours)
        
        return {
            "success": True,
            "summary": summary
        }
    except Exception as e:
        logger.error(f"获取预测摘要失败: {e}")
        return {"success": False, "error": str(e)}

@router.get("/api/predictive/pattern")
async def detect_pattern():
    """检测当前使用模式"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from predictive_analytics import PredictiveEngine
        
        engine = PredictiveEngine(WORKSPACE)
        current = engine.pattern_recognizer.detect_current_pattern()
        next_pattern = engine.pattern_recognizer.predict_next_pattern()
        
        return {
            "success": True,
            "current_pattern": {
                "id": current.pattern_id,
                "name": current.name,
                "description": current.description,
                "associated_tasks": current.associated_tasks
            } if current else None,
            "next_pattern": {
                "id": next_pattern.pattern_id,
                "name": next_pattern.name,
                "expected_within_hours": 2,
                "associated_tasks": next_pattern.associated_tasks[:2]
            } if next_pattern else None,
            "detected_at": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"检测模式失败: {e}")
        return {"success": False, "error": str(e)}

# ========== 自适应学习系统 API ==========

@router.post("/api/adaptive/learn")
async def run_learning_cycle():
    """执行自适应学习周期"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from adaptive_learning import AdaptiveLearningEngine
        
        engine = AdaptiveLearningEngine(WORKSPACE)
        session = engine.run_learning_cycle()
        
        return {
            "success": True,
            "session_id": session.session_id,
            "skills_trained": session.skills_trained,
            "improvements_made": len(session.improvements_made),
            "performance_before": session.performance_before,
            "performance_after": session.performance_after,
            "learning_outcome": session.learning_outcome,
            "duration_seconds": (session.end_time - session.start_time).total_seconds() if session.end_time else None
        }
    except Exception as e:
        logger.error(f"学习周期失败: {e}")
        return {"success": False, "error": str(e)}

@router.get("/api/adaptive/summary")
async def get_learning_summary(days: int = 7):
    """获取自适应学习摘要"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from adaptive_learning import AdaptiveLearningEngine
        
        engine = AdaptiveLearningEngine(WORKSPACE)
        summary = engine.get_learning_summary(days)
        
        return {
            "success": True,
            "summary": summary
        }
    except Exception as e:
        logger.error(f"获取学习摘要失败: {e}")
        return {"success": False, "error": str(e)}

@router.post("/api/adaptive/record")
async def record_skill_usage(request: Request):
    """记录技能使用"""
    try:
        data = await request.json()
        skill_name = data.get("skill_name", "")
        success = data.get("success", True)
        execution_time_ms = data.get("execution_time_ms", 0)
        input_complexity = data.get("input_complexity", "moderate")
        output_quality = data.get("output_quality", 0.8)
        user_satisfaction = data.get("user_satisfaction")
        
        if not skill_name:
            return {"success": False, "error": "技能名称不能为空"}
        
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from adaptive_learning import AdaptiveLearningEngine
        
        engine = AdaptiveLearningEngine(WORKSPACE)
        engine.record_skill_usage(skill_name, success, execution_time_ms,
                                  input_complexity, output_quality, user_satisfaction)
        
        return {
            "success": True,
            "message": f"已记录 {skill_name} 使用",
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"记录技能使用失败: {e}")
        return {"success": False, "error": str(e)}

@router.post("/api/adaptive/optimize/{skill_name}")
async def optimize_skill(skill_name: str):
    """优化特定技能"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from adaptive_learning import AdaptiveLearningEngine
        
        engine = AdaptiveLearningEngine(WORKSPACE)
        result = engine.skill_optimizer.auto_optimize_params(skill_name)
        
        if "error" in result:
            return {"success": False, "error": result["error"]}
        
        return {
            "success": True,
            "skill_name": skill_name,
            "optimizations_applied": result["optimizations_applied"],
            "optimizations": result["optimizations"],
            "current_params": result["current_params"]
        }
    except Exception as e:
        logger.error(f"优化技能失败: {e}")
        return {"success": False, "error": str(e)}

@router.get("/api/adaptive/skill/{skill_name}/stats")
async def get_skill_stats(skill_name: str, days: int = 7):
    """获取技能统计"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from adaptive_learning import AdaptiveLearningEngine
        
        engine = AdaptiveLearningEngine(WORKSPACE)
        stats = engine.pattern_analyzer.analyze_skill_patterns(skill_name, days)
        
        if "error" in stats:
            return {"success": False, "error": stats["error"]}
        
        return {
            "success": True,
            "skill_name": skill_name,
            "period_days": days,
            "stats": stats
        }
    except Exception as e:
        logger.error(f"获取技能统计失败: {e}")
        return {"success": False, "error": str(e)}

# ========== 元学习系统 API ==========

@router.post("/api/meta/insights")
async def generate_meta_insights():
    """生成元学习洞察"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from meta_learning import MetaLearningEngine
        
        engine = MetaLearningEngine(WORKSPACE)
        insights = engine.generate_meta_insights()
        
        return {
            "success": True,
            "insights_count": len(insights),
            "insights": [
                {
                    "type": i.insight_type,
                    "description": i.description,
                    "confidence": i.confidence,
                    "actionable": i.actionable,
                    "recommended_action": i.recommended_action,
                    "expected_improvement": i.expected_improvement
                }
                for i in insights
            ],
            "generated_at": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"生成元学习洞察失败: {e}")
        return {"success": False, "error": str(e)}

@router.post("/api/meta/plan")
async def create_learning_plan(request: Request):
    """创建最优学习计划"""
    try:
        data = await request.json()
        knowledge_items = data.get("knowledge_items", [])
        time_budget = data.get("time_budget", 120)
        
        if not knowledge_items:
            return {"success": False, "error": "请提供学习目标"}
        
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from meta_learning import MetaLearningEngine
        
        engine = MetaLearningEngine(WORKSPACE)
        plan = engine.create_learning_plan(knowledge_items, time_budget)
        
        return {
            "success": True,
            "plan": plan,
            "generated_at": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"创建学习计划失败: {e}")
        return {"success": False, "error": str(e)}

@router.get("/api/meta/report")
async def get_meta_learning_report(days: int = 7):
    """获取元学习效率报告"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from meta_learning import MetaLearningEngine
        
        engine = MetaLearningEngine(WORKSPACE)
        report = engine.get_learning_efficiency_report(days)
        
        return {
            "success": True,
            "report": report
        }
    except Exception as e:
        logger.error(f"获取元学习报告失败: {e}")
        return {"success": False, "error": str(e)}

# ========== 跨域迁移系统 API ==========

@router.post("/api/transfer/execute")
async def execute_knowledge_transfer(request: Request):
    """执行知识迁移"""
    try:
        data = await request.json()
        knowledge_id = data.get("knowledge_id", "")
        target_domain = data.get("target_domain", "")
        adaptation_level = data.get("adaptation_level", "moderate")
        
        if not knowledge_id or not target_domain:
            return {"success": False, "error": "请提供知识ID和目标领域"}
        
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from cross_domain_transfer import KnowledgeTransferEngine, DomainType
        
        engine = KnowledgeTransferEngine(WORKSPACE)
        
        try:
            target = DomainType(target_domain)
        except ValueError:
            return {"success": False, "error": f"无效的领域: {target_domain}"}
        
        result = engine.transfer_knowledge(knowledge_id, target, adaptation_level)
        
        if result:
            return {
                "success": True,
                "transfer_id": result.transfer_id,
                "source_knowledge": result.source_knowledge,
                "target_domain": result.target_domain.value,
                "mapping_confidence": result.mapping_confidence,
                "estimated_effectiveness": result.estimated_effectiveness,
                "adaptation_changes": result.adaptation_changes,
                "transferred_content": result.transferred_content
            }
        else:
            return {"success": False, "error": "迁移失败，无法找到合适的类比映射"}
    except Exception as e:
        logger.error(f"知识迁移失败: {e}")
        return {"success": False, "error": str(e)}

@router.post("/api/transfer/insights")
async def find_transfer_insights(request: Request):
    """发现跨域洞察"""
    try:
        data = await request.json()
        source_domain = data.get("source_domain", "")
        target_domain = data.get("target_domain", "")
        
        if not source_domain or not target_domain:
            return {"success": False, "error": "请提供源领域和目标领域"}
        
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from cross_domain_transfer import KnowledgeTransferEngine, DomainType
        
        engine = KnowledgeTransferEngine(WORKSPACE)
        
        try:
            source = DomainType(source_domain)
            target = DomainType(target_domain)
        except ValueError as e:
            return {"success": False, "error": f"无效的领域: {e}"}
        
        insights = engine.find_cross_domain_insights(source, target)
        
        return {
            "success": True,
            "insights_count": len(insights),
            "insights": insights,
            "generated_at": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"发现跨域洞察失败: {e}")
        return {"success": False, "error": str(e)}

@router.get("/api/transfer/stats")
async def get_transfer_statistics():
    """获取迁移统计"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from cross_domain_transfer import KnowledgeTransferEngine
        
        engine = KnowledgeTransferEngine(WORKSPACE)
        stats = engine.get_transfer_statistics()
        
        return {
            "success": True,
            "statistics": stats
        }
    except Exception as e:
        logger.error(f"获取迁移统计失败: {e}")
        return {"success": False, "error": str(e)}

@router.get("/api/transfer/domains")
async def list_domains():
    """列出可用领域"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from cross_domain_transfer import KnowledgeTransferEngine, DomainType
        
        engine = KnowledgeTransferEngine(WORKSPACE)
        
        domains = []
        for domain in DomainType:
            count = len(engine.knowledge_base.knowledge_by_domain.get(domain, set()))
            domains.append({
                "id": domain.value,
                "name": domain.name,
                "knowledge_count": count
            })
        
        return {
            "success": True,
            "domains": domains,
            "total_domains": len(domains)
        }
    except Exception as e:
        logger.error(f"获取领域列表失败: {e}")
        return {"success": False, "error": str(e)}

# ========== 自主决策系统 API ==========

@router.post("/api/decision/make")
async def make_autonomous_decision(request: Request):
    """执行自主决策"""
    try:
        data = await request.json()
        situation = data.get("situation", "")
        options = data.get("options", [])
        objectives = data.get("objectives", [])
        constraints = data.get("constraints", [])
        auto_execute = data.get("auto_execute", False)
        
        if not situation or not options:
            return {"success": False, "error": "请提供决策情况和选项"}
        
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from autonomous_decision import AutonomousDecisionEngine
        
        engine = AutonomousDecisionEngine(WORKSPACE)
        
        decision = engine.make_decision(
            situation=situation,
            options=options,
            objectives=objectives,
            constraints=constraints,
            auto_execute=auto_execute
        )
        
        return {
            "success": True,
            "decision_id": decision.decision_id,
            "chosen_option": decision.chosen_option,
            "confidence": decision.confidence,
            "risk_level": decision.risk_assessment.overall_risk.name,
            "risk_score": decision.risk_assessment.risk_score,
            "acceptable": decision.risk_assessment.acceptable_risk,
            "expected_outcome": decision.expected_outcome,
            "risk_factors": [
                {
                    "name": f.name,
                    "probability": f.probability,
                    "impact": f.impact,
                    "category": f.category
                }
                for f in decision.risk_assessment.risk_factors
            ],
            "mitigation_suggestions": decision.risk_assessment.mitigation_suggestions,
            "alternative_options": decision.alternative_options,
            "executed": decision.executed,
            "timestamp": decision.timestamp.isoformat()
        }
    except Exception as e:
        logger.error(f"自主决策失败: {e}")
        return {"success": False, "error": str(e)}

@router.post("/api/decision/evaluate")
async def evaluate_decision(request: Request):
    """评估决策效果"""
    try:
        data = await request.json()
        decision_id = data.get("decision_id", "")
        actual_outcome = data.get("actual_outcome", {})
        
        if not decision_id:
            return {"success": False, "error": "请提供决策ID"}
        
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from autonomous_decision import AutonomousDecisionEngine
        
        engine = AutonomousDecisionEngine(WORKSPACE)
        
        result = engine.evaluate_decision(decision_id, actual_outcome)
        
        return {
            "success": True,
            "decision_id": decision_id,
            "accuracy": result.get("accuracy", 0),
            "differences": result.get("differences", {}),
            "success": result.get("success", False),
            "lessons": result.get("lessons", [])
        }
    except Exception as e:
        logger.error(f"评估决策失败: {e}")
        return {"success": False, "error": str(e)}

@router.get("/api/decision/stats")
async def get_decision_statistics():
    """获取决策统计"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from autonomous_decision import AutonomousDecisionEngine
        
        engine = AutonomousDecisionEngine(WORKSPACE)
        stats = engine.get_decision_statistics()
        
        return {
            "success": True,
            "statistics": stats
        }
    except Exception as e:
        logger.error(f"获取决策统计失败: {e}")
        return {"success": False, "error": str(e)}

@router.post("/api/decision/risk")
async def assess_risk(request: Request):
    """风险评估"""
    try:
        data = await request.json()
        situation = data.get("situation", "")
        options = data.get("options", [])
        
        if not situation or not options:
            return {"success": False, "error": "请提供情况和选项"}
        
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from autonomous_decision import AutonomousDecisionEngine, DecisionContext, datetime
        
        engine = AutonomousDecisionEngine(WORKSPACE)
        
        context = DecisionContext(
            context_id=f"risk_{datetime.now().strftime('%H%M%S')}",
            situation=situation,
            available_options=options,
            constraints=[],
            objectives=[],
            time_pressure=data.get("time_pressure", 0.5),
            information_completeness=data.get("information_completeness", 0.8),
            created_at=datetime.now()
        )
        
        assessments = engine.risk_model.assess_risk(context, options)
        
        return {
            "success": True,
            "situation": situation,
            "risk_assessments": {
                option: {
                    "risk_level": a.overall_risk.name,
                    "risk_score": a.risk_score,
                    "acceptable": a.acceptable_risk,
                    "factors_count": len(a.risk_factors),
                    "mitigatable_count": len(a.mitigatable_risks),
                    "suggestions": a.mitigation_suggestions
                }
                for option, a in assessments.items()
            }
        }
    except Exception as e:
        logger.error(f"风险评估失败: {e}")
        return {"success": False, "error": str(e)}

@router.get("/api/decision/actions")
async def get_action_summary():
    """获取行动执行摘要"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from autonomous_decision import AutonomousDecisionEngine
        
        engine = AutonomousDecisionEngine(WORKSPACE)
        summary = engine.action_executor.get_execution_summary()
        
        return {
            "success": True,
            "summary": summary
        }
    except Exception as e:
        logger.error(f"获取行动摘要失败: {e}")
        return {"success": False, "error": str(e)}

# ========== 创造性思维引擎 API ==========

@router.post("/api/creative/generate")
async def generate_creative_solutions(request: Request):
    """生成创造性解决方案"""
    try:
        data = await request.json()
        problem = data.get("problem", "")
        mode = data.get("mode", "hybrid")
        num_solutions = data.get("num_solutions", 5)
        
        if not problem:
            return {"success": False, "error": "请提供问题描述"}
        
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from creative_thinking import CreativeThinkingEngine, CreativeMode
        
        engine = CreativeThinkingEngine(WORKSPACE)
        
        try:
            creative_mode = CreativeMode(mode)
        except ValueError:
            creative_mode = CreativeMode.COMBINATION
        
        solutions = engine.generate_creative_solutions(problem, creative_mode, num_solutions)
        
        return {
            "success": True,
            "problem": problem,
            "mode": mode,
            "solutions_count": len(solutions),
            "solutions": [
                {
                    "idea_id": s.idea_id,
                    "title": s.title,
                    "description": s.description,
                    "mode": s.mode.value,
                    "novelty_score": s.novelty_score,
                    "feasibility_score": s.feasibility_score,
                    "utility_score": s.utility_score,
                    "overall_score": s.overall_score,
                    "components": s.components,
                    "tags": s.tags,
                    "generated_at": s.generated_at.isoformat()
                }
                for s in solutions
            ]
        }
    except Exception as e:
        logger.error(f"生成创意解决方案失败: {e}")
        return {"success": False, "error": str(e)}

@router.post("/api/creative/brainstorm")
async def brainstorm_ideas(request: Request):
    """头脑风暴生成想法"""
    try:
        data = await request.json()
        topic = data.get("topic", "")
        technique = data.get("technique", "scamper")
        num_ideas = data.get("num_ideas", 7)
        
        if not topic:
            return {"success": False, "error": "请提供主题"}
        
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from creative_thinking import CreativeThinkingEngine
        
        engine = CreativeThinkingEngine(WORKSPACE)
        ideas = engine.brainstorming_engine.brainstorm(topic, technique, num_ideas)
        
        return {
            "success": True,
            "topic": topic,
            "technique": technique,
            "ideas_count": len(ideas),
            "ideas": [
                {
                    "idea_id": i.idea_id,
                    "title": i.title,
                    "description": i.description,
                    "mode": i.mode.value,
                    "novelty_score": i.novelty_score,
                    "feasibility_score": i.feasibility_score,
                    "utility_score": i.utility_score,
                    "overall_score": i.overall_score,
                    "components": i.components,
                    "tags": i.tags
                }
                for i in ideas
            ]
        }
    except Exception as e:
        logger.error(f"头脑风暴失败: {e}")
        return {"success": False, "error": str(e)}

@router.post("/api/creative/combine")
async def combine_concepts(request: Request):
    """组合概念生成创新"""
    try:
        data = await request.json()
        concepts = data.get("concepts", [])
        strategy = data.get("strategy", "all")
        
        if len(concepts) < 2:
            return {"success": False, "error": "请提供至少2个概念"}
        
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from creative_thinking import CreativeThinkingEngine
        
        engine = CreativeThinkingEngine(WORKSPACE)
        ideas = engine.combination_engine.combine_concepts(concepts, strategy)
        
        return {
            "success": True,
            "concepts": concepts,
            "strategy": strategy,
            "ideas_count": len(ideas),
            "ideas": [
                {
                    "idea_id": i.idea_id,
                    "title": i.title,
                    "description": i.description,
                    "novelty_score": i.novelty_score,
                    "feasibility_score": i.feasibility_score,
                    "utility_score": i.utility_score,
                    "overall_score": i.overall_score,
                    "components": i.components,
                    "source_concepts": i.source_concepts,
                    "tags": i.tags
                }
                for i in ideas
            ]
        }
    except Exception as e:
        logger.error(f"概念组合失败: {e}")
        return {"success": False, "error": str(e)}

@router.post("/api/creative/analogy")
async def find_analogies(request: Request):
    """寻找类比"""
    try:
        data = await request.json()
        concept = data.get("concept", "")
        source_domain = data.get("source_domain", "technology")
        target_domains = data.get("target_domains", ["nature", "social"])
        
        if not concept:
            return {"success": False, "error": "请提供概念"}
        
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from creative_thinking import CreativeThinkingEngine
        
        engine = CreativeThinkingEngine(WORKSPACE)
        analogies = engine.analogy_engine.find_analogies(concept, source_domain, target_domains)
        
        return {
            "success": True,
            "concept": concept,
            "source_domain": source_domain,
            "target_domains": target_domains,
            "analogies_count": len(analogies),
            "analogies": [
                {
                    "source_concept": a.source_concept,
                    "target_concept": a.target_concept,
                    "source_domain": a.source_domain,
                    "target_domain": a.target_domain,
                    "structural_similarity": a.structural_similarity,
                    "functional_similarity": a.functional_similarity,
                    "explanation": a.mapping_explanation,
                    "insights": a.applicable_insights
                }
                for a in analogies
            ]
        }
    except Exception as e:
        logger.error(f"寻找类比失败: {e}")
        return {"success": False, "error": str(e)}

@router.get("/api/creative/stats")
async def get_creativity_statistics():
    """获取创造性统计"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from creative_thinking import CreativeThinkingEngine
        
        engine = CreativeThinkingEngine(WORKSPACE)
        stats = engine.get_creativity_statistics()
        
        return {
            "success": True,
            "statistics": stats
        }
    except Exception as e:
        logger.error(f"获取创造性统计失败: {e}")
        return {"success": False, "error": str(e)}

# ========== 多智能体协作 API ==========

@router.post("/api/agent/create")
async def create_agent(request: Request):
    """创建智能体"""
    try:
        data = await request.json()
        name = data.get("name", "Agent")
        role = data.get("role", "executor")
        
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from multi_agent_collaboration import MultiAgentSystem, AgentRole
        
        system = MultiAgentSystem(WORKSPACE)
        
        try:
            agent_role = AgentRole(role.lower())
        except ValueError:
            agent_role = AgentRole.EXECUTOR
        
        agent = system.create_agent(name, agent_role)
        
        return {
            "success": True,
            "agent": {
                "agent_id": agent.agent_id,
                "name": agent.name,
                "role": agent.role.value,
                "max_concurrent_tasks": agent.max_concurrent_tasks,
                "is_available": agent.is_available
            }
        }
    except Exception as e:
        logger.error(f"创建智能体失败: {e}")
        return {"success": False, "error": str(e)}

@router.post("/api/agent/task/create")
async def create_agent_task(request: Request):
    """创建任务"""
    try:
        data = await request.json()
        name = data.get("name", "新任务")
        description = data.get("description", "")
        capabilities = data.get("capabilities", [])
        priority = data.get("priority", 1)
        
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from multi_agent_collaboration import MultiAgentSystem
        
        system = MultiAgentSystem(WORKSPACE)
        
        task = system.task_coordinator.create_task(
            name, description, capabilities, priority
        )
        
        return {
            "success": True,
            "task": {
                "task_id": task.task_id,
                "name": task.name,
                "description": task.description,
                "required_capabilities": task.required_capabilities,
                "priority": task.priority,
                "status": task.status.value,
                "created_at": task.created_at.isoformat()
            }
        }
    except Exception as e:
        logger.error(f"创建任务失败: {e}")
        return {"success": False, "error": str(e)}

@router.post("/api/agent/task/assign")
async def assign_agent_task(request: Request):
    """分配任务"""
    try:
        data = await request.json()
        task_id = data.get("task_id")
        agent_id = data.get("agent_id")
        
        if not task_id:
            return {"success": False, "error": "请提供任务ID"}
        
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from multi_agent_collaboration import MultiAgentSystem
        
        system = MultiAgentSystem(WORKSPACE)
        
        task = system.task_coordinator.tasks.get(task_id)
        if not task:
            return {"success": False, "error": f"任务未找到: {task_id}"}
        
        agent = None
        if agent_id:
            agent = system.agents.get(agent_id)
        
        success = system.task_coordinator.assign_task(task, agent)
        
        if success:
            assigned_agent = system.agents.get(task.assigned_agent)
            return {
                "success": True,
                "message": f"任务 {task.name} 已分配给 {assigned_agent.name if assigned_agent else '未知'}",
                "task_id": task.task_id,
                "assigned_agent_id": task.assigned_agent
            }
        else:
            return {"success": False, "error": "任务分配失败"}
    except Exception as e:
        logger.error(f"分配任务失败: {e}")
        return {"success": False, "error": str(e)}

@router.post("/api/agent/message/send")
async def send_agent_message(request: Request):
    """发送消息"""
    try:
        data = await request.json()
        sender_id = data.get("sender_id")
        receiver_id = data.get("receiver_id", "broadcast")
        message_type = data.get("type", "direct")
        content = data.get("content", {})
        
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from multi_agent_collaboration import MultiAgentSystem, MessageType
        
        system = MultiAgentSystem(WORKSPACE)
        
        sender = system.agents.get(sender_id)
        if not sender:
            return {"success": False, "error": f"发送者未找到: {sender_id}"}
        
        try:
            msg_type = MessageType(message_type)
        except ValueError:
            msg_type = MessageType.DIRECT
        
        message = system.comm_protocol.send_message(
            sender, receiver_id, msg_type, content
        )
        
        return {
            "success": True,
            "message_id": message.message_id,
            "sender": sender_id,
            "receiver": receiver_id,
            "type": msg_type.value,
            "timestamp": message.timestamp.isoformat()
        }
    except Exception as e:
        logger.error(f"发送消息失败: {e}")
        return {"success": False, "error": str(e)}

@router.post("/api/agent/conflict/detect")
async def detect_conflicts(request: Request):
    """检测冲突"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from multi_agent_collaboration import MultiAgentSystem
        
        system = MultiAgentSystem(WORKSPACE)
        
        conflicts = system.conflict_resolver.detect_conflict(
            list(system.agents.values()),
            list(system.task_coordinator.tasks.values())
        )
        
        return {
            "success": True,
            "conflicts_detected": len(conflicts),
            "conflicts": [
                {
                    "conflict_id": c.conflict_id,
                    "type": c.conflict_type.value,
                    "severity": c.severity,
                    "description": c.description,
                    "agents_involved": c.agents_involved,
                    "affected_tasks": c.affected_tasks,
                    "status": c.status
                }
                for c in conflicts
            ]
        }
    except Exception as e:
        logger.error(f"检测冲突失败: {e}")
        return {"success": False, "error": str(e)}

@router.post("/api/agent/conflict/resolve")
async def resolve_conflict(request: Request):
    """解决冲突"""
    try:
        data = await request.json()
        conflict_id = data.get("conflict_id")
        strategy = data.get("strategy", "auto")
        
        if not conflict_id:
            return {"success": False, "error": "请提供冲突ID"}
        
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from multi_agent_collaboration import MultiAgentSystem
        
        system = MultiAgentSystem(WORKSPACE)
        
        resolution = system.conflict_resolver.resolve_conflict(conflict_id, strategy)
        
        if resolution:
            return {
                "success": True,
                "conflict_id": conflict_id,
                "resolution": resolution,
                "status": "resolved"
            }
        else:
            return {"success": False, "error": "冲突解决失败"}
    except Exception as e:
        logger.error(f"解决冲突失败: {e}")
        return {"success": False, "error": str(e)}

@router.get("/api/agent/status")
async def get_agent_system_status():
    """获取多智能体系统状态"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from multi_agent_collaboration import MultiAgentSystem
        
        system = MultiAgentSystem(WORKSPACE)
        
        status = system.get_system_status()
        
        return {
            "success": True,
            "status": {
                "is_running": status["is_running"],
                "agents_count": status["agents_count"],
                "tasks": status["tasks_status"],
                "conflicts": status["conflicts"],
                "communication": status["communication"],
                "uptime": status["uptime"]
            }
        }
    except Exception as e:
        logger.error(f"获取多智能体状态失败: {e}")
        return {"success": False, "error": str(e)}

# ========== 长期目标规划 API ==========

@router.post("/api/goal/create")
async def create_long_term_goal(request: Request):
    """创建长期目标"""
    try:
        data = await request.json()
        name = data.get("name", "新目标")
        description = data.get("description", "")
        vision = data.get("vision", "")
        priority = data.get("priority", "medium")
        estimated_days = data.get("estimated_days", 90)
        strategy = data.get("strategy", "balanced")
        
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from long_term_goal_planner import LongTermGoalPlanner
        
        planner = LongTermGoalPlanner(WORKSPACE)
        result = planner.create_and_plan_goal(
            name=name,
            description=description,
            vision=vision,
            priority=priority,
            estimated_days=estimated_days,
            decomposition_strategy=strategy
        )
        
        return result
    except Exception as e:
        logger.error(f"创建长期目标失败: {e}")
        return {"success": False, "error": str(e)}

@router.get("/api/goal/{goal_id}/status")
async def get_goal_status(goal_id: str):
    """获取目标状态"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from long_term_goal_planner import LongTermGoalPlanner
        
        planner = LongTermGoalPlanner(WORKSPACE)
        status = planner.get_goal_status(goal_id)
        
        return status
    except Exception as e:
        logger.error(f"获取目标状态失败: {e}")
        return {"success": False, "error": str(e)}

@router.post("/api/goal/subgoal/update")
async def update_subgoal_progress(request: Request):
    """更新子目标进度"""
    try:
        data = await request.json()
        subgoal_id = data.get("subgoal_id")
        progress = data.get("progress", 0)
        actual_hours = data.get("actual_hours")
        
        if not subgoal_id:
            return {"success": False, "error": "请提供子目标ID"}
        
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from long_term_goal_planner import LongTermGoalPlanner
        
        planner = LongTermGoalPlanner(WORKSPACE)
        success = planner.update_subgoal_progress(subgoal_id, progress, actual_hours)
        
        if success:
            return {"success": True, "message": f"进度已更新为 {progress}%"}
        else:
            return {"success": False, "error": "子目标不存在"}
    except Exception as e:
        logger.error(f"更新进度失败: {e}")
        return {"success": False, "error": str(e)}

@router.get("/api/goal/list")
async def list_goals():
    """列出所有目标"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from long_term_goal_planner import LongTermGoalPlanner
        
        planner = LongTermGoalPlanner(WORKSPACE)
        summary = planner.get_all_goals_summary()
        
        return summary
    except Exception as e:
        logger.error(f"列出目标失败: {e}")
        return {"success": False, "error": str(e)}

@router.post("/api/goal/{goal_id}/suggestions")
async def get_goal_suggestions(goal_id: str):
    """获取目标调整建议"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from long_term_goal_planner import LongTermGoalPlanner
        
        planner = LongTermGoalPlanner(WORKSPACE)
        goal = planner.hierarchy_manager.goals.get(goal_id)
        
        if not goal:
            return {"success": False, "error": "目标不存在"}
        
        suggestions = planner.progress_evaluator.suggest_adjustments(goal)
        
        return {
            "success": True,
            "goal_id": goal_id,
            "goal_name": goal.name,
            "suggestions": suggestions
        }
    except Exception as e:
        logger.error(f"获取建议失败: {e}")
        return {"success": False, "error": str(e)}

# ========== 情感智能增强 API ==========

@router.post("/api/emotion/analyze")
async def analyze_emotion(request: Request):
    """分析用户输入的情感和语调"""
    try:
        data = await request.json()
        text = data.get("text", "")
        user_id = data.get("user_id", "default_user")
        
        if not text:
            return {"success": False, "error": "请提供文本内容"}
        
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from emotional_intelligence import EmotionalIntelligenceEngine
        
        engine = EmotionalIntelligenceEngine()
        analysis = engine.analyze_input(text, user_id)
        
        return {
            "success": True,
            "analysis": analysis
        }
    except Exception as e:
        logger.error(f"情感分析失败: {e}")
        return {"success": False, "error": str(e)}

@router.post("/api/emotion/respond")
async def generate_emotional_response(request: Request):
    """生成个性化共情响应"""
    try:
        data = await request.json()
        text = data.get("text", "")
        base_response = data.get("base_response", "")
        user_id = data.get("user_id", "default_user")
        
        if not text or not base_response:
            return {"success": False, "error": "请提供文本和基础响应"}
        
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from emotional_intelligence import EmotionalIntelligenceEngine
        
        engine = EmotionalIntelligenceEngine()
        
        # 先分析情感
        analysis = engine.analyze_input(text, user_id)
        
        # 生成个性化响应
        personalized = engine.generate_response(base_response, user_id, analysis)
        
        return {
            "success": True,
            "original_text": text,
            "emotion": analysis["emotion"],
            "tone": analysis["tone"],
            "personalized_response": personalized
        }
    except Exception as e:
        logger.error(f"生成情感响应失败: {e}")
        return {"success": False, "error": str(e)}

@router.get("/api/emotion/profile/{user_id}")
async def get_emotion_profile(user_id: str):
    """获取用户情感档案"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from emotional_intelligence import EmotionalIntelligenceEngine
        
        engine = EmotionalIntelligenceEngine()
        profile = engine.get_user_emotion_summary(user_id)
        
        if profile:
            return {
                "success": True,
                "profile": profile
            }
        else:
            return {
                "success": False,
                "error": "用户没有情感档案记录"
            }
    except Exception as e:
        logger.error(f"获取情感档案失败: {e}")
        return {"success": False, "error": str(e)}

@router.delete("/api/emotion/profile/{user_id}")
async def reset_emotion_profile(user_id: str):
    """重置用户情感档案"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from emotional_intelligence import EmotionalIntelligenceEngine
        
        engine = EmotionalIntelligenceEngine()
        engine.reset_user_profile(user_id)
        
        return {
            "success": True,
            "message": f"用户 {user_id} 的情感档案已重置"
        }
    except Exception as e:
        logger.error(f"重置情感档案失败: {e}")
        return {"success": False, "error": str(e)}

@router.get("/api/emotion/status")
async def get_emotion_engine_status():
    """获取情感智能引擎状态"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from emotional_intelligence import (
            EmotionType, ToneType, EmotionRecognitionModel, 
            ToneAnalyzer, EmpathyEngine
        )
        
        return {
            "success": True,
            "status": {
                "engine_version": "1.0.0",
                "emotion_types": [e.value for e in EmotionType],
                "tone_types": [t.value for t in ToneType],
                "modules": {
                    "emotion_recognition": "active",
                    "tone_analysis": "active",
                    "response_personalization": "active",
                    "empathy_engine": "active"
                }
            }
        }
    except Exception as e:
        logger.error(f"获取情感引擎状态失败: {e}")
        return {"success": False, "error": str(e)}

# ========== 神经符号集成推理 API ==========

@router.post("/api/reasoning/hybrid")
async def hybrid_reasoning(request: Request):
    """执行神经符号混合推理"""
    try:
        data = await request.json()
        query = data.get("query", "")
        reasoning_type = data.get("type", "hybrid")
        
        if not query:
            return {"success": False, "error": "请提供查询内容"}
        
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from neuro_symbolic_reasoning import (
            NeuroSymbolicReasoningEngine, ReasoningType
        )
        
        engine = NeuroSymbolicReasoningEngine()
        
        # 转换推理类型
        r_type = None
        if reasoning_type in ["deductive", "inductive", "abductive", "analogical", "causal"]:
            r_type = ReasoningType(reasoning_type)
        
        # 执行推理
        result = engine.reason(query, r_type)
        
        return {
            "success": True,
            "result": result.to_dict()
        }
    except Exception as e:
        logger.error(f"混合推理失败: {e}")
        return {"success": False, "error": str(e)}

@router.post("/api/reasoning/explain")
async def explain_reasoning(request: Request):
    """生成推理过程解释"""
    try:
        data = await request.json()
        query = data.get("query", "")
        
        if not query:
            return {"success": False, "error": "请提供查询内容"}
        
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from neuro_symbolic_reasoning import NeuroSymbolicReasoningEngine
        
        engine = NeuroSymbolicReasoningEngine()
        
        # 执行推理
        result = engine.reason(query)
        
        # 生成详细解释
        explanation = engine.explain(result)
        
        return {
            "success": True,
            "query": query,
            "explanation": explanation,
            "result_summary": {
                "conclusion": result.conclusion,
                "confidence": result.confidence,
                "reasoning_type": result.reasoning_type.value,
                "steps_count": len(result.steps),
                "execution_time_ms": result.execution_time_ms
            }
        }
    except Exception as e:
        logger.error(f"推理解释失败: {e}")
        return {"success": False, "error": str(e)}

@router.get("/api/reasoning/knowledge/stats")
async def get_knowledge_stats():
    """获取知识库统计信息"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from neuro_symbolic_reasoning import NeuroSymbolicReasoningEngine
        
        engine = NeuroSymbolicReasoningEngine()
        stats = engine.get_knowledge_stats()
        
        return {
            "success": True,
            "stats": stats
        }
    except Exception as e:
        logger.error(f"获取知识库统计失败: {e}")
        return {"success": False, "error": str(e)}

@router.post("/api/reasoning/knowledge/add")
async def add_knowledge(request: Request):
    """添加知识到知识图谱"""
    try:
        data = await request.json()
        entity_name = data.get("name", "")
        entity_type = data.get("type", "")
        properties = data.get("properties", {})
        
        if not entity_name or not entity_type:
            return {"success": False, "error": "请提供实体名称和类型"}
        
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from neuro_symbolic_reasoning import NeuroSymbolicReasoningEngine
        
        engine = NeuroSymbolicReasoningEngine()
        entity = engine.add_knowledge(entity_name, entity_type, properties)
        
        return {
            "success": True,
            "entity": entity.to_dict()
        }
    except Exception as e:
        logger.error(f"添加知识失败: {e}")
        return {"success": False, "error": str(e)}

@router.get("/api/reasoning/status")
async def get_reasoning_engine_status():
    """获取神经符号推理引擎状态"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from neuro_symbolic_reasoning import (
            NeuroSymbolicReasoningEngine, ReasoningType, RelationType
        )
        from causal_reasoning import CausalReasoningEngine, CausalAlgorithm
        
        engine = NeuroSymbolicReasoningEngine()
        stats = engine.get_knowledge_stats()
        
        # 获取因果推理引擎状态
        causal_engine = CausalReasoningEngine()
        causal_graphs = causal_engine.list_graphs()
        
        return {
            "success": True,
            "status": {
                "engine_version": "1.0.0",
                "reasoning_types": [t.value for t in ReasoningType],
                "relation_types": [t.value for t in RelationType],
                "modules": {
                    "knowledge_graph": "active",
                    "symbolic_reasoner": "active",
                    "neural_symbolic_integrator": "active",
                    "explanation_generator": "active",
                    "causal_reasoning": "active",
                    "counterfactual": "active",
                    "intervention_prediction": "active"
                },
                "knowledge_base": stats,
                "causal_graphs": causal_graphs
            }
        }
    except Exception as e:
        logger.error(f"获取推理引擎状态失败: {e}")
        return {"success": False, "error": str(e)}

# ========== 因果推理 API ==========

@router.post("/api/causal/discover")
async def causal_discover(request: Request):
    """因果发现 - 从数据中学习因果结构"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from causal_reasoning import CausalReasoningEngine, CausalAlgorithm
        
        data = await request.json()
        variables = data.get("variables", [])
        dataset = data.get("data", {})
        algorithm = data.get("algorithm", "pc")
        
        engine = CausalReasoningEngine()
        algo = CausalAlgorithm.PC if algorithm == "pc" else CausalAlgorithm.GRANGER
        
        graph = engine.discover(
            data=dataset,
            variable_names=variables,
            algorithm=algo,
            graph_name=f"discovered_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        
        return {
            "success": True,
            "graph_id": graph.graph_id,
            "name": graph.name,
            "nodes": [{"id": n.id, "name": n.name} for n in graph.nodes.values()],
            "edges": [{"source": e.source_id, "target": e.target_id, 
                      "strength": e.strength} for e in graph.edges]
        }
    except Exception as e:
        logger.error(f"因果发现失败: {e}")
        return {"success": False, "error": str(e)}

@router.post("/api/causal/counterfactual")
async def causal_counterfactual(request: Request):
    """反事实推理 - '如果...会怎样'分析"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from causal_reasoning import CausalReasoningEngine, Intervention, InterventionType
        
        data = await request.json()
        graph_id = data.get("graph_id")
        target_var = data.get("target_var")
        factual = data.get("factual_evidence", {})
        intervention_data = data.get("intervention", {})
        
        engine = CausalReasoningEngine()
        intervention = Intervention(
            intervention_id=f"cf_{datetime.now().strftime('%H%M%S')}",
            target_var=intervention_data.get("target_var"),
            intervention_type=InterventionType.DO,
            new_value=intervention_data.get("new_value"),
            description=intervention_data.get("description", "")
        )
        
        result = engine.run_counterfactual(graph_id, target_var, intervention, factual)
        
        return {
            "success": True,
            "factual_outcome": result.factual_outcome,
            "counterfactual_outcome": result.counterfactual_outcome,
            "individual_effect": result.individual_effect,
            "explanation": result.explanation,
            "confidence": result.confidence
        }
    except Exception as e:
        logger.error(f"反事实推理失败: {e}")
        return {"success": False, "error": str(e)}

@router.post("/api/causal/intervention")
async def causal_intervention(request: Request):
    """干预效果预测 - 预测行动后果"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from causal_reasoning import CausalReasoningEngine
        
        data = await request.json()
        graph_id = data.get("graph_id")
        treatment = data.get("treatment_var")
        outcome = data.get("outcome_var")
        do_value = data.get("do_value", 1.0)
        
        engine = CausalReasoningEngine()
        effect = engine.predict_intervention(graph_id, treatment, do_value, outcome)
        
        return {
            "success": True,
            "effect_type": effect.effect_type,
            "source_var": effect.source_var,
            "target_var": effect.target_var,
            "effect_size": effect.effect_size,
            "confidence_interval": list(effect.confidence_interval),
            "method": effect.method
        }
    except Exception as e:
        logger.error(f"干预预测失败: {e}")
        return {"success": False, "error": str(e)}

@router.get("/api/causal/graphs")
async def list_causal_graphs():
    """列出所有因果图"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from causal_reasoning import CausalReasoningEngine
        
        engine = CausalReasoningEngine()
        graphs = engine.list_graphs()
        
        return {
            "success": True,
            "graphs": graphs
        }
    except Exception as e:
        logger.error(f"获取因果图列表失败: {e}")
        return {"success": False, "error": str(e)}

# ========== 终身学习 API ==========

@router.post("/api/lifelong/task/start")
async def start_lifelong_task(request: Request):
    """开始新的终身学习任务"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from lifelong_learning import LifelongLearningEngine, TaskType
        
        data = await request.json()
        task_id = data.get("task_id", f"task_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        task_type_str = data.get("task_type", "knowledge")
        
        engine = LifelongLearningEngine()
        task_type = TaskType(task_type_str)
        column = engine.start_new_task(task_id, task_type)
        
        return {
            "success": True,
            "task_id": task_id,
            "column_id": column.column_id,
            "task_type": task_type.value,
            "message": f"任务 {task_id} 已启动"
        }
    except Exception as e:
        logger.error(f"启动终身学习任务失败: {e}")
        return {"success": False, "error": str(e)}

@router.post("/api/lifelong/learn")
async def learn_knowledge(request: Request):
    """学习新知识单元"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from lifelong_learning import LifelongLearningEngine, TaskType
        
        data = await request.json()
        content = data.get("content", "")
        task_type_str = data.get("task_type", "knowledge")
        importance = data.get("importance", 0.5)
        
        if not content:
            return {"success": False, "error": "内容不能为空"}
        
        engine = LifelongLearningEngine()
        task_type = TaskType(task_type_str)
        unit = engine.learn_knowledge_unit(content, task_type, importance)
        
        return {
            "success": True,
            "unit_id": unit.unit_id,
            "content": unit.content,
            "importance": unit.importance_score,
            "created_at": unit.created_at.isoformat()
        }
    except Exception as e:
        logger.error(f"学习知识失败: {e}")
        return {"success": False, "error": str(e)}

@router.post("/api/lifelong/replay")
async def replay_experience(request: Request):
    """触发经验重放"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from lifelong_learning import LifelongLearningEngine
        
        data = await request.json()
        n_samples = data.get("n_samples", 32)
        strategy = data.get("strategy", "balanced")
        
        engine = LifelongLearningEngine()
        samples = engine.review_and_replay(n_samples, strategy)
        
        return {
            "success": True,
            "n_samples": len(samples),
            "samples": [
                {
                    "sample_id": s.sample_id,
                    "task_id": s.task_id,
                    "importance": s.importance,
                    "retrieval_count": s.retrieval_count
                }
                for s in samples
            ]
        }
    except Exception as e:
        logger.error(f"经验重放失败: {e}")
        return {"success": False, "error": str(e)}

@router.post("/api/lifelong/task/consolidate")
async def consolidate_task(request: Request):
    """巩固已完成的任务"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from lifelong_learning import LifelongLearningEngine
        
        data = await request.json()
        task_id = data.get("task_id")
        
        if not task_id:
            return {"success": False, "error": "task_id 不能为空"}
        
        engine = LifelongLearningEngine()
        
        # 模拟参数和梯度
        params = {f"param_{i}": random.uniform(-1, 1) for i in range(10)}
        grads = {k: [random.uniform(-0.1, 0.1) for _ in range(5)] for k in params}
        
        engine.consolidate_task(task_id, params, grads)
        
        return {
            "success": True,
            "task_id": task_id,
            "message": f"任务 {task_id} 已巩固"
        }
    except Exception as e:
        logger.error(f"巩固任务失败: {e}")
        return {"success": False, "error": str(e)}

@router.get("/api/lifelong/report")
async def get_learning_report():
    """获取学习报告"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from lifelong_learning import LifelongLearningEngine
        
        engine = LifelongLearningEngine()
        report = engine.get_learning_report()
        
        return {
            "success": True,
            "report": report
        }
    except Exception as e:
        logger.error(f"获取学习报告失败: {e}")
        return {"success": False, "error": str(e)}

# ========== 高级自然语言理解 API ==========

@router.post("/api/nlu/understand")
async def advanced_nlu_understand(request: Request):
    """高级自然语言理解 - 深度语义分析"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from advanced_nlu import AdvancedNLUEngine
        
        data = await request.json()
        text = data.get("text", "")
        context_id = data.get("context_id", "default")
        
        if not text:
            return {"success": False, "error": "文本不能为空"}
        
        engine = AdvancedNLUEngine()
        result = engine.understand(text, context_id)
        
        return {
            "success": True,
            "analysis": result
        }
    except Exception as e:
        logger.error(f"NLU分析失败: {e}")
        return {"success": False, "error": str(e)}

@router.post("/api/nlu/srl")
async def semantic_role_labeling(request: Request):
    """语义角色标注"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from advanced_nlu import SemanticRoleLabeler
        
        data = await request.json()
        text = data.get("text", "")
        
        if not text:
            return {"success": False, "error": "文本不能为空"}
        
        labeler = SemanticRoleLabeler()
        frames = labeler.label(text)
        
        return {
            "success": True,
            "frames": [
                {
                    "predicate": f.predicate,
                    "type": f.predicate_type,
                    "arguments": [{"role": a.role.value, "text": a.text} for a in f.arguments],
                    "modifiers": [{"role": m.role.value, "text": m.text} for m in f.modifiers]
                }
                for f in frames
            ]
        }
    except Exception as e:
        logger.error(f"SRL分析失败: {e}")
        return {"success": False, "error": str(e)}

@router.post("/api/nlu/coreference")
async def coreference_resolution(request: Request):
    """指代消解"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from advanced_nlu import CoreferenceResolver
        
        data = await request.json()
        text = data.get("text", "")
        context_id = data.get("context_id", "default")
        
        if not text:
            return {"success": False, "error": "文本不能为空"}
        
        resolver = CoreferenceResolver()
        mentions, context = resolver.resolve(text, context_id)
        
        return {
            "success": True,
            "mentions": [
                {
                    "text": m.text,
                    "type": m.reference_type.value,
                    "resolved_to": m.resolved_to
                }
                for m in mentions
            ],
            "entities": [
                {
                    "id": e.entity_id,
                    "form": e.canonical_form,
                    "mentions_count": len(e.mentions)
                }
                for e in context.entities.values()
            ]
        }
    except Exception as e:
        logger.error(f"指代消解失败: {e}")
        return {"success": False, "error": str(e)}

@router.post("/api/nlu/implicature")
async def implicature_inference(request: Request):
    """隐含意义推断"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from advanced_nlu import ImplicatureInferencer
        
        data = await request.json()
        text = data.get("text", "")
        
        if not text:
            return {"success": False, "error": "文本不能为空"}
        
        inferencer = ImplicatureInferencer()
        implicatures = inferencer.infer(text)
        
        return {
            "success": True,
            "implicatures": [
                {
                    "type": i.implicature_type.value,
                    "surface_meaning": i.surface_meaning,
                    "inferred_meaning": i.inferred_meaning,
                    "confidence": i.confidence,
                    "evidence": i.evidence
                }
                for i in implicatures
            ]
        }
    except Exception as e:
        logger.error(f"隐含意义推断失败: {e}")
        return {"success": False, "error": str(e)}

# ========== 创造性问题解决 API ==========

@router.post("/api/creative/solve")
async def creative_solve(request: Request):
    """创造性问题求解 - 综合类比、组合、发散思维"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from creative_problem_solving import CreativeProblemSolvingEngine
        
        data = await request.json()
        problem = data.get("problem", "")
        approach = data.get("approach", "hybrid")
        
        if not problem:
            return {"success": False, "error": "问题描述不能为空"}
        
        engine = CreativeProblemSolvingEngine()
        results = engine.solve_creatively(problem, approach)
        
        return {
            "success": True,
            "problem": problem,
            "approach": approach,
            "solutions": results["solutions"]
        }
    except Exception as e:
        logger.error(f"创造性求解失败: {e}")
        return {"success": False, "error": str(e)}

@router.post("/api/creative/analogy")
async def creative_analogy(request: Request):
    """类比推理 - 跨领域知识迁移"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from creative_problem_solving import AnalogyReasoningEngine, Domain
        
        data = await request.json()
        source_domain = data.get("source_domain")
        target_problem = data.get("target_problem")
        
        if not target_problem:
            return {"success": False, "error": "目标问题不能为空"}
        
        engine = AnalogyReasoningEngine()
        mappings = engine.find_analogy(source_domain or "general", target_problem)
        
        return {
            "success": True,
            "analogies": [
                {
                    "mapping_id": m.mapping_id,
                    "source": m.source_domain,
                    "target": m.target_domain,
                    "type": m.analogy_type.value,
                    "confidence": m.confidence,
                    "explanation": m.explanation,
                    "correspondences": m.correspondences
                }
                for m in mappings[:3]
            ]
        }
    except Exception as e:
        logger.error(f"类比推理失败: {e}")
        return {"success": False, "error": str(e)}

@router.post("/api/creative/combine")
async def creative_combine(request: Request):
    """组合创新 - 元素重组生成方案"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from creative_problem_solving import CombinatorialInnovation
        
        data = await request.json()
        problem = data.get("problem", "")
        n_solutions = data.get("n_solutions", 5)
        
        engine = CombinatorialInnovation()
        combinations = engine.generate_combinations(problem, n_solutions)
        
        return {
            "success": True,
            "combinations": [
                {
                    "id": c.combination_id,
                    "elements": c.elements,
                    "strategy": c.strategy.value,
                    "description": c.result_description,
                    "novelty": c.novelty_score,
                    "feasibility": c.feasibility_score,
                    "value": c.value_score
                }
                for c in combinations
            ]
        }
    except Exception as e:
        logger.error(f"组合创新失败: {e}")
        return {"success": False, "error": str(e)}

@router.post("/api/creative/divergent")
async def creative_divergent(request: Request):
    """发散思维 - 生成多样化解决方案"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from creative_problem_solving import DivergentThinking, DivergentTechnique
        
        data = await request.json()
        problem = data.get("problem", "")
        technique_str = data.get("technique")
        n_solutions = data.get("n_solutions", 5)
        
        if not problem:
            return {"success": False, "error": "问题描述不能为空"}
        
        engine = DivergentThinking()
        
        technique = None
        if technique_str:
            technique = DivergentTechnique(technique_str)
        
        solutions = engine.generate_divergent_solutions(
            problem, technique, n_solutions
        )
        
        return {
            "success": True,
            "solutions": [
                {
                    "id": s.solution_id,
                    "description": s.solution_description,
                    "technique": s.technique.value,
                    "uniqueness": s.uniqueness_score,
                    "potential_impact": s.potential_impact
                }
                for s in solutions
            ]
        }
    except Exception as e:
        logger.error(f"发散思维失败: {e}")
        return {"success": False, "error": str(e)}

@router.post("/api/creative/evaluate")
async def creative_evaluate(request: Request):
    """创新方案评估"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from creative_problem_solving import CreativeProblemSolvingEngine
        
        data = await request.json()
        solution = data.get("solution", "")
        
        if not solution:
            return {"success": False, "error": "方案描述不能为空"}
        
        engine = CreativeProblemSolvingEngine()
        score = engine.evaluate_innovation(solution)
        
        return {
            "success": True,
            "evaluation": {
                "novelty": score.novelty,
                "usefulness": score.usefulness,
                "feasibility": score.feasibility,
                "elegance": score.elegance,
                "surprise": score.surprise,
                "total_score": score.total_score
            }
        }
    except Exception as e:
        logger.error(f"创新评估失败: {e}")
        return {"success": False, "error": str(e)}

# ========== 系统集成枢纽 API ==========

@router.post("/api/unified/query")
async def unified_query(request: Request):
    """统一查询 - 多模块联合推理"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from system_integration_hub import SystemIntegrationHub
        
        data = await request.json()
        query = data.get("query", "")
        context = data.get("context", {})
        
        if not query:
            return {"success": False, "error": "查询不能为空"}
        
        hub = SystemIntegrationHub()
        result = hub.unified_query(query, context)
        
        return {
            "success": True,
            "query": query,
            "modules_used": result["modules_used"],
            "overall_confidence": result["overall_confidence"],
            "stages": result["stages"],
            "timestamp": result["timestamp"]
        }
    except Exception as e:
        logger.error(f"统一查询失败: {e}")
        return {"success": False, "error": str(e)}

@router.get("/api/system/status")
async def system_status():
    """全系统状态检查"""
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "core"))
        from system_integration_hub import SystemIntegrationHub
        
        hub = SystemIntegrationHub()
        status = hub.get_system_status()
        
        return {
            "success": True,
            "integration_health": status["integration_health"],
            "modules": status["modules"],
            "timestamp": status["timestamp"]
        }
    except Exception as e:
        logger.error(f"系统状态检查失败: {e}")
        return {"success": False, "error": str(e)}

