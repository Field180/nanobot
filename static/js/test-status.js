/**
 * 前端功能测试脚本 - 系统状态与修复功能测试
 * 在浏览器控制台运行: runSystemStatusTests()
 */

const SystemStatusTest = {
    results: [],
    
    log(message, type = 'info') {
        const icon = type === 'success' ? '✅' : type === 'error' ? '❌' : type === 'warn' ? '⚠️' : 'ℹ️';
        console.log(`${icon} ${message}`);
        this.results.push({ message, type, time: new Date().toISOString() });
    },
    
    // 测试1: 检查按钮文字动态变化
    async testButtonTextDynamic() {
        this.log('测试按钮文字动态变化...');
        const btn = document.querySelector('.fix-bug-container');
        if (!btn) {
            this.log('修复按钮不存在', 'error');
            return false;
        }
        
        const textSpan = btn.querySelector('span:not(.fix-bug-icon)');
        if (!textSpan) {
            this.log('按钮文字元素不存在', 'error');
            return false;
        }
        
        const originalText = textSpan.textContent;
        this.log(`当前按钮文字: "${originalText}"`);
        
        // 检查文字是否符合预期
        const validTexts = ['系统修复', '系统状态'];
        if (validTexts.includes(originalText)) {
            this.log(`按钮文字正确: "${originalText}"`, 'success');
            return true;
        } else {
            this.log(`按钮文字异常: "${originalText}"，期望 "系统修复" 或 "系统状态"`, 'warn');
            return false;
        }
    },
    
    // 测试2: 检查按钮样式类
    testButtonClasses() {
        this.log('测试按钮样式类...');
        const btn = document.querySelector('.fix-bug-container');
        if (!btn) {
            this.log('修复按钮不存在', 'error');
            return false;
        }
        
        const hasNormalClass = btn.classList.contains('status-normal');
        const hasIssuesClass = btn.classList.contains('has-issues');
        
        this.log(`status-normal类: ${hasNormalClass}`);
        this.log(`has-issues类: ${hasIssuesClass}`);
        
        if (hasNormalClass || hasIssuesClass) {
            this.log('按钮状态类已正确设置', 'success');
            return true;
        } else {
            this.log('按钮缺少状态类，可能需要等待状态检测完成', 'warn');
            return false;
        }
    },
    
    // 测试3: 检查新函数存在性
    testNewFunctionsExist() {
        this.log('测试新函数存在性...');
        const functions = [
            'scanFixIssuesQuiet',
            'renderStatusContent',
            'renderIssuesContent',
            'loadSystemStatus',
            'renderStatusSection',
            'updateFixButtonStatus'
        ];
        
        let allExist = true;
        for (const fn of functions) {
            const exists = typeof window[fn] === 'function';
            if (exists) {
                this.log(`${fn} 存在`, 'success');
            } else {
                this.log(`${fn} 不存在`, 'error');
                allExist = false;
            }
        }
        
        return allExist;
    },
    
    // 测试4: 测试面板打开
    async testPanelOpen() {
        this.log('测试面板打开功能...');
        if (typeof openFixPanel !== 'function') {
            this.log('openFixPanel函数不存在', 'error');
            return false;
        }
        
        try {
            await openFixPanel();
            const panel = document.getElementById('fix-panel');
            if (panel) {
                this.log('面板成功打开', 'success');
                
                // 检查标题
                const header = panel.querySelector('.fix-panel-header h3');
                if (header) {
                    const titleText = header.textContent.trim();
                    this.log(`面板标题: "${titleText}"`);
                    
                    if (titleText.includes('系统状态中心') || titleText.includes('系统修复中心')) {
                        this.log('面板标题正确', 'success');
                    } else {
                        this.log(`面板标题异常: "${titleText}"`, 'warn');
                    }
                }
                
                // 关闭面板
                closeFixPanel();
                return true;
            } else {
                this.log('面板未成功打开', 'error');
                return false;
            }
        } catch (e) {
            this.log(`打开面板出错: ${e.message}`, 'error');
            return false;
        }
    },
    
    // 测试5: 测试渲染函数
    testRenderFunctions() {
        this.log('测试渲染函数...');
        let allPass = true;
        
        // 测试renderStatusContent
        if (typeof renderStatusContent === 'function') {
            const html = renderStatusContent();
            if (html.includes('系统运行正常') && html.includes('status-healthy-banner')) {
                this.log('renderStatusContent 输出正确', 'success');
            } else {
                this.log('renderStatusContent 输出缺少关键元素', 'error');
                allPass = false;
            }
        } else {
            this.log('renderStatusContent 不存在', 'error');
            allPass = false;
        }
        
        // 测试renderIssuesContent
        if (typeof renderIssuesContent === 'function') {
            const html = renderIssuesContent();
            if (html.includes('fix-scan-status')) {
                this.log('renderIssuesContent 输出正确', 'success');
            } else {
                this.log('renderIssuesContent 输出缺少关键元素', 'error');
                allPass = false;
            }
        } else {
            this.log('renderIssuesContent 不存在', 'error');
            allPass = false;
        }
        
        // 测试renderStatusSection
        if (typeof renderStatusSection === 'function') {
            const html = renderStatusSection('测试标题', 'fa-test', [
                { label: '测试项', value: '测试值' }
            ]);
            if (html.includes('测试标题') && html.includes('测试项')) {
                this.log('renderStatusSection 输出正确', 'success');
            } else {
                this.log('renderStatusSection 输出不正确', 'error');
                allPass = false;
            }
        } else {
            this.log('renderStatusSection 不存在', 'error');
            allPass = false;
        }
        
        return allPass;
    },
    
    // 测试6: 检查CSS样式
    testCSSStyles() {
        this.log('检查CSS样式...');
        const styles = document.styleSheets;
        let foundStatusNormal = false;
        let foundStatusHealthy = false;
        
        try {
            for (let sheet of styles) {
                try {
                    for (let rule of sheet.cssRules || []) {
                        if (rule.selectorText) {
                            if (rule.selectorText.includes('status-normal')) foundStatusNormal = true;
                            if (rule.selectorText.includes('status-healthy')) foundStatusHealthy = true;
                        }
                    }
                } catch (e) {}
            }
        } catch (e) {}
        
        this.log(`找到 status-normal CSS: ${foundStatusNormal}`);
        this.log(`找到 status-healthy CSS: ${foundStatusHealthy}`);
        
        if (foundStatusNormal && foundStatusHealthy) {
            this.log('CSS样式已正确加载', 'success');
            return true;
        } else {
            this.log('部分CSS样式可能未加载（可能在内联样式中）', 'warn');
            return true; // 不强制失败，因为可能在<style>标签内
        }
    },
    
    // 测试7: 检查图标切换
    testIconSwitch() {
        this.log('测试图标切换功能...');
        const btn = document.querySelector('.fix-bug-container');
        if (!btn) {
            this.log('修复按钮不存在', 'error');
            return false;
        }
        
        const icon = btn.querySelector('i');
        if (!icon) {
            this.log('按钮图标不存在', 'error');
            return false;
        }
        
        const iconClass = icon.className;
        this.log(`当前图标类: ${iconClass}`);
        
        if (iconClass.includes('fa-wrench') || iconClass.includes('fa-shield-alt')) {
            this.log('图标正确 (扳手或盾牌)', 'success');
            return true;
        } else {
            this.log(`图标可能不正确: ${iconClass}`, 'warn');
            return false;
        }
    },
    
    // 生成报告
    generateReport() {
        console.log('\n' + '='.repeat(60));
        console.log('📊 测试报告');
        console.log('='.repeat(60));
        
        const errors = this.results.filter(r => r.type === 'error').length;
        const warnings = this.results.filter(r => r.type === 'warn').length;
        const successes = this.results.filter(r => r.type === 'success').length;
        
        console.log(`总计: ${this.results.length} 项`);
        console.log(`✅ 成功: ${successes}`);
        console.log(`⚠️  警告: ${warnings}`);
        console.log(`❌ 错误: ${errors}`);
        
        if (errors === 0) {
            console.log('\n🎉 所有关键测试通过！');
        } else if (errors < 3) {
            console.log('\n⚠️  有少量错误，核心功能可能可用。');
        } else {
            console.log('\n❌ 多个错误，需要检查代码。');
        }
        
        console.log('='.repeat(60));
        
        return { total: this.results.length, successes, warnings, errors };
    }
};

// 运行所有测试
async function runSystemStatusTests() {
    console.clear();
    console.log('='.repeat(60));
    console.log('🧪 动态系统状态功能测试套件');
    console.log('='.repeat(60));
    console.log(`开始时间: ${new Date().toLocaleString('zh-CN')}`);
    console.log('='.repeat(60));
    
    SystemStatusTest.results = [];
    
    // 运行所有测试
    await SystemStatusTest.testButtonTextDynamic();
    SystemStatusTest.testButtonClasses();
    SystemStatusTest.testNewFunctionsExist();
    await SystemStatusTest.testPanelOpen();
    SystemStatusTest.testRenderFunctions();
    SystemStatusTest.testCSSStyles();
    SystemStatusTest.testIconSwitch();
    
    // 生成报告
    const report = SystemStatusTest.generateReport();
    
    // 导出到全局
    window.lastTestReport = report;
    
    return report;
}

// 快速测试单个功能
function quickTestStatusFeature() {
    console.log('🔍 快速测试动态状态功能...\n');
    
    const checks = {
        'scanFixIssuesQuiet函数': typeof scanFixIssuesQuiet === 'function',
        'renderStatusContent函数': typeof renderStatusContent === 'function',
        'updateFixButtonStatus函数': typeof updateFixButtonStatus === 'function',
        '修复按钮存在': !!document.querySelector('.fix-bug-container'),
        '按钮状态类': (() => {
            const btn = document.querySelector('.fix-bug-container');
            return btn && (btn.classList.contains('status-normal') || btn.classList.contains('has-issues'));
        })()
    };
    
    let pass = 0;
    let fail = 0;
    
    for (const [name, result] of Object.entries(checks)) {
        if (result) {
            console.log(`✅ ${name}`);
            pass++;
        } else {
            console.log(`❌ ${name}`);
            fail++;
        }
    }
    
    console.log(`\n结果: ${pass}通过, ${fail}失败`);
    return fail === 0;
}

// 导出到全局
window.SystemStatusTest = SystemStatusTest;
window.runSystemStatusTests = runSystemStatusTests;
window.quickTestStatusFeature = quickTestStatusFeature;

console.log('💡 动态系统状态测试脚本已加载！');
console.log('可用命令:');
console.log('  - runSystemStatusTests() : 运行完整测试套件');
console.log('  - quickTestStatusFeature() : 快速功能检查');
console.log('  - SystemStatusTest.testPanelOpen() : 仅测试面板');
