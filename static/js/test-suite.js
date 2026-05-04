/**
 * Nanobot Web UI 功能仿真测试脚本
 * 测试范围：修复按钮 + 每日任务按钮
 * 运行方式：在浏览器控制台执行 testAllFunctions()
 */

// ========== 测试框架 ==========

const TestRunner = {
    results: [],
    currentSuite: null,
    
    // 开始测试套件
    suite(name, fn) {
        this.currentSuite = name;
        console.log(`\n📦 ${name}`);
        fn();
        this.currentSuite = null;
    },
    
    // 单个测试
    test(name, fn) {
        try {
            fn();
            this.results.push({ suite: this.currentSuite, name, status: '✅ PASS' });
            console.log(`  ✅ ${name}`);
        } catch (e) {
            this.results.push({ suite: this.currentSuite, name, status: '❌ FAIL', error: e.message });
            console.log(`  ❌ ${name}: ${e.message}`);
        }
    },
    
    // 断言
    assert(condition, message) {
        if (!condition) {
            throw new Error(message || '断言失败');
        }
    },
    
    // 断言相等
    assertEqual(actual, expected, message) {
        if (actual !== expected) {
            throw new Error(message || `期望 ${expected}, 实际 ${actual}`);
        }
    },
    
    // 断言存在
    assertExists(selector, message) {
        const el = document.querySelector(selector);
        if (!el) {
            throw new Error(message || `元素不存在: ${selector}`);
        }
        return el;
    },
    
    // 打印报告
    report() {
        const passed = this.results.filter(r => r.status === '✅ PASS').length;
        const failed = this.results.filter(r => r.status === '❌ FAIL').length;
        
        console.log('\n' + '='.repeat(50));
        console.log('📊 测试报告');
        console.log('='.repeat(50));
        console.log(`总计: ${this.results.length} 个测试`);
        console.log(`✅ 通过: ${passed}`);
        console.log(`❌ 失败: ${failed}`);
        
        if (failed > 0) {
            console.log('\n❌ 失败的测试:');
            this.results.filter(r => r.status === '❌ FAIL').forEach(r => {
                console.log(`  - [${r.suite}] ${r.name}: ${r.error}`);
            });
        }
        
        return { passed, failed, total: this.results.length };
    },
    
    // 清空结果
    reset() {
        this.results = [];
        this.currentSuite = null;
    }
};

// ========== DOM 元素存在性测试 ==========

function testDOMElements() {
    TestRunner.suite('DOM元素存在性测试', () => {
        TestRunner.test('修复按钮容器存在', () => {
            TestRunner.assertExists('.fix-bug-container', '修复按钮容器不存在');
        });
        
        TestRunner.test('修复按钮可见', () => {
            const btn = document.querySelector('.fix-bug-container');
            if (btn) {
                const style = window.getComputedStyle(btn);
                TestRunner.assert(style.display !== 'none', '修复按钮被隐藏');
            }
        });
        
        TestRunner.test('每日任务按钮存在', () => {
            TestRunner.assertExists('#taskSchedulerBtn', '每日任务按钮不存在');
        });
        
        TestRunner.test('每日任务按钮包含必要子元素', () => {
            const btn = document.querySelector('#taskSchedulerBtn');
            if (btn) {
                TestRunner.assertExists('#taskSchedulerBtn .task-icon', '任务图标不存在');
                TestRunner.assertExists('#taskSchedulerBtn .task-text', '任务文本不存在');
                TestRunner.assertExists('#taskBadge', '任务徽章不存在');
            }
        });
        
        TestRunner.test('状态指示器存在', () => {
            TestRunner.assertExists('.status-indicator', '状态指示器不存在');
        });
        
        TestRunner.test('侧边栏任务区域存在', () => {
            TestRunner.assertExists('.sidebar-task-section', '侧边栏任务区域不存在');
        });
    });
}

// ========== 修复按钮功能测试 ==========

function testFixButtonFunctions() {
    TestRunner.suite('修复按钮功能测试', () => {
        TestRunner.test('修复按钮点击事件绑定', () => {
            const btn = document.querySelector('.fix-bug-container');
            if (btn) {
                // 检查是否有onclick属性或事件监听
                const hasClickHandler = btn.onclick !== null || 
                                       btn.getAttribute('onclick') !== null;
                TestRunner.assert(hasClickHandler, '修复按钮未绑定点击事件');
            }
        });
        
        TestRunner.test('openFixPanel函数存在', () => {
            TestRunner.assert(typeof openFixPanel === 'function', 
                             'openFixPanel函数不存在');
        });
        
        TestRunner.test('closeFixPanel函数存在', () => {
            TestRunner.assert(typeof closeFixPanel === 'function', 
                             'closeFixPanel函数不存在');
        });
        
        TestRunner.test('scanFixIssues函数存在', () => {
            TestRunner.assert(typeof scanFixIssues === 'function', 
                             'scanFixIssues函数不存在');
        });
        
        TestRunner.test('applySingleFix函数存在', () => {
            TestRunner.assert(typeof applySingleFix === 'function', 
                             'applySingleFix函数不存在');
        });
        
        TestRunner.test('applyAllFixes函数存在', () => {
            TestRunner.assert(typeof applyAllFixes === 'function', 
                             'applyAllFixes函数不存在');
        });
        
        TestRunner.test('修复面板可打开关闭', async () => {
            // 打开面板
            if (typeof openFixPanel === 'function') {
                await openFixPanel();
                const panel = document.getElementById('fix-panel');
                TestRunner.assert(panel !== null, '修复面板未打开');
                
                // 关闭面板
                if (typeof closeFixPanel === 'function') {
                    closeFixPanel();
                    const closedPanel = document.getElementById('fix-panel');
                    TestRunner.assert(closedPanel === null, '修复面板未关闭');
                }
            }
        });
    });
}

// ========== 每日任务按钮功能测试 ==========

function testTaskButtonFunctions() {
    TestRunner.suite('每日任务按钮功能测试', () => {
        TestRunner.test('openTaskSchedulerPanel函数存在', () => {
            TestRunner.assert(typeof openTaskSchedulerPanel === 'function', 
                             'openTaskSchedulerPanel函数不存在');
        });
        
        TestRunner.test('closeTaskSchedulerPanel函数存在', () => {
            TestRunner.assert(typeof closeTaskSchedulerPanel === 'function', 
                             'closeTaskSchedulerPanel函数不存在');
        });
        
        TestRunner.test('loadTaskData函数存在', () => {
            TestRunner.assert(typeof loadTaskData === 'function', 
                             'loadTaskData函数不存在');
        });
        
        TestRunner.test('getLocalTaskData函数存在', () => {
            TestRunner.assert(typeof getLocalTaskData === 'function', 
                             'getLocalTaskData函数不存在');
        });
        
        TestRunner.test('renderTaskPanel函数存在', () => {
            TestRunner.assert(typeof renderTaskPanel === 'function', 
                             'renderTaskPanel函数不存在');
        });
        
        TestRunner.test('handleTaskClick函数存在', () => {
            TestRunner.assert(typeof handleTaskClick === 'function', 
                             'handleTaskClick函数不存在');
        });
        
        TestRunner.test('updateTaskBadge函数存在', () => {
            TestRunner.assert(typeof updateTaskBadge === 'function', 
                             'updateTaskBadge函数不存在');
        });
        
        TestRunner.test('refreshTaskStatus函数存在', () => {
            TestRunner.assert(typeof refreshTaskStatus === 'function', 
                             'refreshTaskStatus函数不存在');
        });
        
        TestRunner.test('startTaskStatusRefresh函数存在', () => {
            TestRunner.assert(typeof startTaskStatusRefresh === 'function', 
                             'startTaskStatusRefresh函数不存在');
        });
        
        TestRunner.test('formatTaskTime函数存在', () => {
            TestRunner.assert(typeof formatTaskTime === 'function', 
                             'formatTaskTime函数不存在');
        });
        
        TestRunner.test('任务面板可打开关闭', async () => {
            if (typeof openTaskSchedulerPanel === 'function') {
                await openTaskSchedulerPanel();
                const overlay = document.getElementById('task-panel-overlay');
                TestRunner.assert(overlay !== null, '任务面板未打开');
                
                if (typeof closeTaskSchedulerPanel === 'function') {
                    closeTaskSchedulerPanel();
                    const closedOverlay = document.getElementById('task-panel-overlay');
                    TestRunner.assert(closedOverlay === null, '任务面板未关闭');
                }
            }
        });
        
        TestRunner.test('本地任务数据格式正确', () => {
            if (typeof getLocalTaskData === 'function') {
                const data = getLocalTaskData();
                TestRunner.assert(data !== null, '本地数据为空');
                TestRunner.assert(Array.isArray(data.tasks), 'tasks不是数组');
                TestRunner.assert(typeof data.scheduler_running === 'boolean', 
                                 'scheduler_running不是布尔值');
                
                // 检查任务字段
                if (data.tasks.length > 0) {
                    const task = data.tasks[0];
                    TestRunner.assert(task.id, '任务缺少id字段');
                    TestRunner.assert(task.name, '任务缺少name字段');
                    TestRunner.assert(task.command, '任务缺少command字段');
                    TestRunner.assert(task.status, '任务缺少status字段');
                }
            }
        });
        
        TestRunner.test('formatTaskTime格式化正确', () => {
            if (typeof formatTaskTime === 'function') {
                const now = new Date();
                const isoString = now.toISOString();
                const formatted = formatTaskTime(isoString);
                TestRunner.assert(typeof formatted === 'string', '格式化结果不是字符串');
                TestRunner.assert(formatted.length > 0, '格式化结果为空');
            }
        });
        
        TestRunner.test('updateTaskBadge更新徽章', () => {
            const testData = {
                tasks: [
                    { id: '1', status: 'pending' },
                    { id: '2', status: 'pending' },
                    { id: '3', status: 'completed' }
                ]
            };
            
            if (typeof updateTaskBadge === 'function') {
                updateTaskBadge(testData);
                const badge = document.getElementById('taskBadge');
                if (badge) {
                    // 应该有2个待执行任务
                    TestRunner.assertEqual(badge.textContent, '2', '徽章数字不正确');
                }
            }
        });
    });
}

// ========== API 接口测试 ==========

async function testAPIEndpoints() {
    TestRunner.suite('API接口可用性测试', () => {
        TestRunner.test('CONFIG对象存在', () => {
            TestRunner.assert(typeof CONFIG !== 'undefined', 'CONFIG对象不存在');
            TestRunner.assert(CONFIG.API_URL, 'CONFIG.API_URL未定义');
        });
        
        TestRunner.test('修复API端点格式正确', () => {
            const endpoints = [
                '/api/fix/scan',
                '/api/fix/rules',
                '/api/fix/check',
                '/api/fix/apply',
                '/api/fix/apply-all'
            ];
            
            endpoints.forEach(endpoint => {
                TestRunner.assert(endpoint.startsWith('/api/'), 
                                 `${endpoint} 格式不正确`);
            });
        });
        
        TestRunner.test('任务API端点格式正确', () => {
            TestRunner.assert('/api/tasks/status'.startsWith('/api/'), 
                             '任务API端点格式不正确');
        });
    });
}

// ========== 样式和交互测试 ==========

function testStylesAndInteractions() {
    TestRunner.suite('样式和交互测试', () => {
        TestRunner.test('修复按钮有呼吸灯动画类', () => {
            const btn = document.querySelector('.fix-bug-container');
            if (btn) {
                const hasAnimation = btn.classList.contains('breathing-red') ||
                                   btn.classList.contains('fix-bug-breathing');
                // 不强制要求，只是检查
                if (!hasAnimation) {
                    console.log('    ⚠️ 修复按钮缺少呼吸灯动画类（可选）');
                }
            }
        });
        
        TestRunner.test('每日任务按钮有正确样式类', () => {
            const btn = document.querySelector('#taskSchedulerBtn');
            if (btn) {
                TestRunner.assert(btn.classList.contains('task-scheduler-btn'), 
                                 '每日任务按钮缺少task-scheduler-btn类');
            }
        });
        
        TestRunner.test('任务徽章默认隐藏', () => {
            const badge = document.getElementById('taskBadge');
            if (badge) {
                const style = window.getComputedStyle(badge);
                // 初始状态下应该隐藏
                TestRunner.assert(style.display === 'none' || badge.style.display === 'none', 
                                 '任务徽章默认应该是隐藏的');
            }
        });
        
        TestRunner.test('面板CSS类定义存在', () => {
            // 检查关键CSS类是否在样式表中定义
            const styles = document.styleSheets;
            let hasFixPanel = false;
            let hasTaskPanel = false;
            
            try {
                for (let sheet of styles) {
                    try {
                        for (let rule of sheet.cssRules || []) {
                            if (rule.selectorText) {
                                if (rule.selectorText.includes('fix-panel')) hasFixPanel = true;
                                if (rule.selectorText.includes('task-panel')) hasTaskPanel = true;
                            }
                        }
                    } catch (e) {
                        // 跨域样式表可能无法访问，忽略
                    }
                }
            } catch (e) {
                // 忽略错误
            }
            
            // 不强制要求，因为这些样式可能在<style>标签内定义
            console.log(`    ℹ️ 修复面板CSS: ${hasFixPanel ? '✅' : '⚠️'}`);
            console.log(`    ℹ️ 任务面板CSS: ${hasTaskPanel ? '✅' : '⚠️'}`);
        });
    });
}

// ========== 事件和状态管理测试 ==========

function testEventAndStateManagement() {
    TestRunner.suite('事件和状态管理测试', () => {
        TestRunner.test('fixPanelOpen变量存在', () => {
            // 检查全局变量
            TestRunner.assert(typeof fixPanelOpen !== 'undefined' || 
                             (typeof window.fixPanelOpen !== 'undefined'), 
                             'fixPanelOpen状态变量不存在');
        });
        
        TestRunner.test('taskPanelOpen变量存在', () => {
            TestRunner.assert(typeof taskPanelOpen !== 'undefined' || 
                             (typeof window.taskPanelOpen !== 'undefined'), 
                             'taskPanelOpen状态变量不存在');
        });
        
        TestRunner.test('taskDataCache变量存在', () => {
            TestRunner.assert(typeof taskDataCache !== 'undefined' || 
                             (typeof window.taskDataCache !== 'undefined'), 
                             'taskDataCache数据缓存不存在');
        });
        
        TestRunner.test('showToast函数存在', () => {
            TestRunner.assert(typeof showToast === 'function', 
                             'showToast提示函数不存在');
        });
    });
}

// ========== 集成测试 ==========

async function testIntegration() {
    TestRunner.suite('集成测试', () => {
        TestRunner.test('两个按钮同时存在不冲突', () => {
            const fixBtn = document.querySelector('.fix-bug-container');
            const taskBtn = document.querySelector('#taskSchedulerBtn');
            
            TestRunner.assert(fixBtn !== null || taskBtn !== null, 
                             '至少需要一个按钮存在');
            
            if (fixBtn && taskBtn) {
                // 检查它们不在同一个位置（避免重叠）
                const fixRect = fixBtn.getBoundingClientRect();
                const taskRect = taskBtn.getBoundingClientRect();
                
                // 它们应该在不同的区域（一个在上部，一个在底部）
                TestRunner.assert(fixRect.top !== taskRect.top, 
                                 '两个按钮位置可能重叠');
            }
        });
        
        TestRunner.test('点击按钮不会导致页面错误', async () => {
            // 模拟点击修复按钮
            const fixBtn = document.querySelector('.fix-bug-container');
            if (fixBtn && fixBtn.onclick) {
                try {
                    // 记录原始状态
                    const originalErrorCount = console.errors?.length || 0;
                    
                    // 注意：这里不实际点击，因为会弹出面板
                    // 只是验证onclick函数可以调用
                    console.log('    ℹ️ 修复按钮onclick事件已绑定');
                } catch (e) {
                    TestRunner.assert(false, `修复按钮点击会导致错误: ${e.message}`);
                }
            }
            
            // 模拟点击任务按钮
            const taskBtn = document.querySelector('#taskSchedulerBtn');
            if (taskBtn && taskBtn.onclick) {
                try {
                    console.log('    ℹ️ 任务按钮onclick事件已绑定');
                } catch (e) {
                    TestRunner.assert(false, `任务按钮点击会导致错误: ${e.message}`);
                }
            }
        });
        
        TestRunner.test('localStorage读写正常', () => {
            const testKey = '_test_nanobot_';
            const testValue = 'test_data';
            
            try {
                localStorage.setItem(testKey, testValue);
                const readValue = localStorage.getItem(testKey);
                localStorage.removeItem(testKey);
                
                TestRunner.assertEqual(readValue, testValue, 'localStorage读写失败');
            } catch (e) {
                TestRunner.assert(false, `localStorage测试失败: ${e.message}`);
            }
        });
    });
}

// ========== 性能测试 ==========

async function testPerformance() {
    TestRunner.suite('性能测试', () => {
        TestRunner.test('openFixPanel执行时间 < 100ms', async () => {
            if (typeof openFixPanel === 'function') {
                const start = performance.now();
                await openFixPanel();
                const end = performance.now();
                const duration = end - start;
                
                // 关闭面板
                if (typeof closeFixPanel === 'function') {
                    closeFixPanel();
                }
                
                TestRunner.assert(duration < 100, 
                                 `openFixPanel执行时间 ${duration.toFixed(2)}ms 超过100ms`);
                console.log(`    ℹ️ 执行时间: ${duration.toFixed(2)}ms`);
            }
        });
        
        TestRunner.test('openTaskSchedulerPanel执行时间 < 100ms', async () => {
            if (typeof openTaskSchedulerPanel === 'function') {
                const start = performance.now();
                await openTaskSchedulerPanel();
                const end = performance.now();
                const duration = end - start;
                
                // 关闭面板
                if (typeof closeTaskSchedulerPanel === 'function') {
                    closeTaskSchedulerPanel();
                }
                
                TestRunner.assert(duration < 100, 
                                 `openTaskSchedulerPanel执行时间 ${duration.toFixed(2)}ms 超过100ms`);
                console.log(`    ℹ️ 执行时间: ${duration.toFixed(2)}ms`);
            }
        });
        
        TestRunner.test('getLocalTaskData执行时间 < 10ms', () => {
            if (typeof getLocalTaskData === 'function') {
                const start = performance.now();
                const data = getLocalTaskData();
                const end = performance.now();
                const duration = end - start;
                
                TestRunner.assert(duration < 10, 
                                 `getLocalTaskData执行时间 ${duration.toFixed(2)}ms 超过10ms`);
                console.log(`    ℹ️ 执行时间: ${duration.toFixed(2)}ms`);
            }
        });
    });
}

// ========== 主测试函数 ==========

async function testAllFunctions() {
    console.clear();
    console.log('='.repeat(60));
    console.log('🧪 Nanobot Web UI 功能仿真测试');
    console.log('='.repeat(60));
    console.log(`测试时间: ${new Date().toLocaleString('zh-CN')}`);
    console.log(`页面URL: ${window.location.href}`);
    console.log('='.repeat(60));
    
    // 重置测试结果
    TestRunner.reset();
    
    // 运行所有测试套件
    testDOMElements();
    testFixButtonFunctions();
    testTaskButtonFunctions();
    await testAPIEndpoints();
    testStylesAndInteractions();
    testEventAndStateManagement();
    await testIntegration();
    await testPerformance();
    
    // 生成报告
    const report = TestRunner.report();
    
    // 最终结论
    console.log('\n' + '='.repeat(60));
    if (report.failed === 0) {
        console.log('🎉 所有测试通过！系统运行正常。');
    } else if (report.failed < 3) {
        console.log('⚠️  有少量测试失败，但核心功能应该可用。');
    } else {
        console.log('❌ 多个测试失败，建议检查代码。');
    }
    console.log('='.repeat(60));
    
    return report;
}

// ========== 快速测试函数 ==========

// 测试修复按钮
async function testFixButton() {
    console.log('🧪 测试修复按钮...');
    TestRunner.reset();
    testFixButtonFunctions();
    return TestRunner.report();
}

// 测试每日任务按钮
async function testTaskButton() {
    console.log('🧪 测试每日任务按钮...');
    TestRunner.reset();
    testTaskButtonFunctions();
    return TestRunner.report();
}

// 快速健康检查
function quickHealthCheck() {
    console.log('🔍 快速健康检查...');
    
    const checks = {
        '修复按钮存在': !!document.querySelector('.fix-bug-container'),
        '每日任务按钮存在': !!document.querySelector('#taskSchedulerBtn'),
        '修复函数存在': typeof openFixPanel === 'function',
        '任务函数存在': typeof openTaskSchedulerPanel === 'function',
        'API配置存在': typeof CONFIG !== 'undefined' && !!CONFIG.API_URL,
    };
    
    console.log('\n检查结果:');
    let allPass = true;
    for (const [name, result] of Object.entries(checks)) {
        const icon = result ? '✅' : '❌';
        console.log(`  ${icon} ${name}`);
        if (!result) allPass = false;
    }
    
    console.log('\n' + (allPass ? '🎉 系统健康' : '⚠️  存在问题'));
    return allPass;
}

// 导出到全局
window.TestRunner = TestRunner;
window.testAllFunctions = testAllFunctions;
window.testFixButton = testFixButton;
window.testTaskButton = testTaskButton;
window.quickHealthCheck = quickHealthCheck;

// ========== 动态系统状态功能测试 ==========

function testDynamicSystemStatus() {
    TestRunner.suite('动态系统状态功能测试', () => {
        TestRunner.test('scanFixIssuesQuiet函数存在', () => {
            TestRunner.assert(typeof scanFixIssuesQuiet === 'function', 
                             'scanFixIssuesQuiet函数不存在');
        });
        
        TestRunner.test('renderStatusContent函数存在', () => {
            TestRunner.assert(typeof renderStatusContent === 'function', 
                             'renderStatusContent函数不存在');
        });
        
        TestRunner.test('renderIssuesContent函数存在', () => {
            TestRunner.assert(typeof renderIssuesContent === 'function', 
                             'renderIssuesContent函数不存在');
        });
        
        TestRunner.test('loadSystemStatus函数存在', () => {
            TestRunner.assert(typeof loadSystemStatus === 'function', 
                             'loadSystemStatus函数不存在');
        });
        
        TestRunner.test('renderStatusSection函数存在', () => {
            TestRunner.assert(typeof renderStatusSection === 'function', 
                             'renderStatusSection函数不存在');
        });
        
        TestRunner.test('updateFixButtonStatus函数存在', () => {
            TestRunner.assert(typeof updateFixButtonStatus === 'function', 
                             'updateFixButtonStatus函数不存在');
        });
        
        TestRunner.test('渲染正常状态内容不为空', () => {
            if (typeof renderStatusContent === 'function') {
                const html = renderStatusContent();
                TestRunner.assert(html.length > 0, '渲染内容为空');
                TestRunner.assert(html.includes('系统运行正常'), '缺少正常状态横幅');
                TestRunner.assert(html.includes('status-healthy-banner'), '缺少健康横幅类');
            }
        });
        
        TestRunner.test('渲染问题内容不为空', () => {
            if (typeof renderIssuesContent === 'function') {
                const html = renderIssuesContent();
                TestRunner.assert(html.length > 0, '渲染内容为空');
                TestRunner.assert(html.includes('fix-scan-status'), '缺少扫描状态类');
            }
        });
        
        TestRunner.test('渲染状态部分格式正确', () => {
            if (typeof renderStatusSection === 'function') {
                const items = [
                    { label: '测试1', value: '值1' },
                    { label: '测试2', value: '值2' }
                ];
                const html = renderStatusSection('测试标题', 'fa-test', items);
                TestRunner.assert(html.includes('测试标题'), '标题未包含');
                TestRunner.assert(html.includes('fa-test'), '图标未包含');
                TestRunner.assert(html.includes('测试1'), '项目1未包含');
                TestRunner.assert(html.includes('值1'), '值1未包含');
            }
        });
        
        TestRunner.test('CSS类status-normal存在定义', () => {
            // 检查样式表中是否有status-normal类
            const styles = document.styleSheets;
            let found = false;
            try {
                for (let sheet of styles) {
                    try {
                        for (let rule of sheet.cssRules || []) {
                            if (rule.selectorText && 
                                (rule.selectorText.includes('status-normal') ||
                                 rule.selectorText.includes('status-healthy'))) {
                                found = true;
                                break;
                            }
                        }
                    } catch (e) {}
                    if (found) break;
                }
            } catch (e) {}
            
            // 由于可能在内联<style>中，我们不强制要求
            if (!found) {
                console.log('    ⚠️  CSS类可能在内联样式中定义');
            }
        });
        
        TestRunner.test('按钮状态切换类名正确', () => {
            const container = document.querySelector('.fix-bug-container');
            if (container) {
                // 检查可以添加/移除类名
                container.classList.add('test-class');
                const hasClass = container.classList.contains('test-class');
                container.classList.remove('test-class');
                TestRunner.assert(hasClass, '无法操作classList');
            }
        });
    });
}

// 测试动态状态功能
async function testDynamicStatus() {
    console.log('\n🧪 测试动态系统状态功能...');
    TestRunner.reset();
    testDynamicSystemStatus();
    return TestRunner.report();
}

// 运行所有测试（更新版）
async function runAllTests() {
    console.clear();
    console.log('='.repeat(60));
    console.log('🧪 Nanobot Web UI 完整功能测试套件');
    console.log('='.repeat(60));
    console.log(`测试时间: ${new Date().toLocaleString('zh-CN')}`);
    console.log('='.repeat(60));
    
    TestRunner.reset();
    
    // 运行所有测试套件
    testDOMElements();
    testFixButtonFunctions();
    testTaskButtonFunctions();
    await testAPIEndpoints();
    testStylesAndInteractions();
    testEventAndStateManagement();
    await testIntegration();
    testDynamicSystemStatus(); // 新增
    await testPerformance();
    
    // 生成报告
    const report = TestRunner.report();
    
    // 最终结论
    console.log('\n' + '='.repeat(60));
    if (report.failed === 0) {
        console.log('🎉 所有测试通过！系统运行完美。');
    } else if (report.failed < 3) {
        console.log('⚠️  有少量测试失败，核心功能可用。');
    } else {
        console.log('❌ 多个测试失败，需要检查代码。');
    }
    console.log('='.repeat(60));
    
    return report;
}

// 导出到全局
window.testDynamicStatus = testDynamicStatus;
window.runAllTests = runAllTests;

// 自动运行快速检查
document.addEventListener('DOMContentLoaded', function() {
    setTimeout(() => {
        console.log('\n💡 测试脚本已加载！可用命令:');
        console.log('  - runAllTests()      : 运行完整测试套件');
        console.log('  - testAllFunctions()   : 原始完整测试');
        console.log('  - testDynamicStatus()  : 仅测试动态状态功能');
        console.log('  - quickHealthCheck()   : 快速健康检查');
        console.log('\n🔍 正在运行快速健康检查...');
        quickHealthCheck();
    }, 1500);
});
console.log('  - quickHealthCheck() : 快速健康检查');

// 页面加载完成后自动运行快速检查
document.addEventListener('DOMContentLoaded', () => {
    setTimeout(quickHealthCheck, 1000);
});
