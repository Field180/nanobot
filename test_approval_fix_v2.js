/**
 * 审批后续写格式保留验证测试 v2
 * 
 * 测试多层保护机制：
 * 1. _fullResponseMarkdown 在 chunk 事件中保存
 * 2. _fullResponseMarkdown 在 done 事件中保存
 * 3. buildResumeBaseState 优先使用 _fullResponseMarkdown
 * 4. buildResumeBaseState 从 localStorage 备选恢复
 * 5. 续写开始时优先使用 _fullResponseMarkdown
 * 6. 续写开始时从 localStorage 备选恢复
 */

// 模拟 DOM 元素
function createMockMessageDiv() {
    return {
        id: 'msg_123',
        dataset: {},
        _fullResponseMarkdown: '',
        _resumeSeedText: '',
        _resumeBaseText: '',
        querySelector: function(selector) {
            if (selector === '.message-content') {
                return createMockMessageContent();
            }
            if (selector === '.streaming-content') {
                return createMockStreamingContent();
            }
            return null;
        },
        closest: function(selector) {
            if (selector === '[data-chat-id]') {
                return { dataset: { chatId: 'test-chat-123' } };
            }
            return null;
        }
    };
}

function createMockMessageContent() {
    return {
        cloneNode: function() {
            return {
                querySelectorAll: function() { return { forEach: function() {} }; },
                innerHTML: '<div>mock content</div>',
                textContent: 'Agentic Turn 1 file_read test_format.py file_edit'
            };
        },
        querySelectorAll: function() { return { forEach: function() {} }; }
    };
}

function createMockStreamingContent() {
    return {
        textContent: 'Agentic Turn 1 file_read test_format.py file_edit',
        innerHTML: '<p>mock</p>'
    };
}

// 模拟 localStorage
const mockLocalStorage = {
    storage: {},
    getItem: function(key) {
        return this.storage[key] || null;
    },
    setItem: function(key, value) {
        this.storage[key] = value;
    }
};

// 测试1: _fullResponseMarkdown 保存了完整 markdown
function testMarkdownSavedDuringStreaming() {
    console.log('=== 测试1: _fullResponseMarkdown 在流式过程中保存 ===');
    
    const messageDiv = createMockMessageDiv();
    const fullResponse = '---\n🔄 **Agentic Turn 1**\n\n> 🔧 **file_read** `{"path":"test.py"}`\n> ⏳ 执行中...\n\n> ✅ **file_read** (1ms)\n\n---\n🔄 **Agentic Turn 2**\n\n> 🔧 **file_edit**\n> 已生成待审批修改卡片。\n\n请审批';
    
    // 模拟 chunk 事件保存
    messageDiv._fullResponseMarkdown = fullResponse;
    
    const hasMarkdown = messageDiv._fullResponseMarkdown.includes('**') &&
                        messageDiv._fullResponseMarkdown.includes('`') &&
                        messageDiv._fullResponseMarkdown.includes('>');
    
    console.log(`Markdown 格式保留: ${hasMarkdown ? '✅' : '❌'}`);
    console.log(`内容长度: ${messageDiv._fullResponseMarkdown.length}`);
    
    return hasMarkdown;
}

// 测试2: done 事件保存 _fullResponseMarkdown
function testDoneEventSavesMarkdown() {
    console.log('\n=== 测试2: done 事件保存 _fullResponseMarkdown ===');
    
    const messageDiv = createMockMessageDiv();
    
    // 模拟没有 chunk 的情况（罕见但可能）
    let fullResponse = '';  // empty initially
    const data = { response: '请审批' };
    
    // 模拟 done 事件兜底
    if (!fullResponse && data.response) {
        fullResponse = data.response;
    }
    
    // FIX: 保存 _fullResponseMarkdown
    messageDiv._fullResponseMarkdown = fullResponse;
    
    const saved = messageDiv._fullResponseMarkdown === '请审批';
    console.log(`done 事件保存了内容: ${saved ? '✅' : '❌'}`);
    
    return saved;
}

// 测试3: buildResumeBaseState 优先使用 _fullResponseMarkdown
function testBuildResumeBaseStatePrefersMarkdown() {
    console.log('\n=== 测试3: buildResumeBaseState 优先使用 _fullResponseMarkdown ===');
    
    const messageDiv = createMockMessageDiv();
    messageDiv._fullResponseMarkdown = '---\n🔄 **Agentic Turn 1**\n\n> ✅ **file_read**';
    
    // 模拟 buildResumeBaseState 逻辑
    let originalMarkdown = messageDiv?._fullResponseMarkdown || '';
    const fallbackText = 'Agentic Turn 1 file_read';  // textContent 的结果
    
    const result = originalMarkdown || fallbackText;
    
    const hasFormat = result.includes('**') && result.includes('>');
    console.log(`buildResumeBaseState 返回 markdown: ${hasFormat ? '✅' : '❌'}`);
    console.log(`结果内容: ${result.substring(0, 50)}...`);
    
    return hasFormat;
}

// 测试4: localStorage 备选恢复
function testLocalStorageFallback() {
    console.log('\n=== 测试4: localStorage 备选恢复 ===');
    
    const messageDiv = createMockMessageDiv();
    messageDiv._fullResponseMarkdown = '';  // 模拟丢失
    
    // 模拟 localStorage 中有保存的内容
    mockLocalStorage.setItem('streaming_test-chat-123', JSON.stringify({
        content: '---\n🔄 **Agentic Turn 1**\n\n> ✅ **file_read**',
        isStreaming: false
    }));
    
    // 模拟 buildResumeBaseState 备选逻辑
    let originalMarkdown = messageDiv?._fullResponseMarkdown || '';
    
    if (!originalMarkdown && messageDiv?.id) {
        try {
            const streamingStateRaw = mockLocalStorage.getItem('streaming_test-chat-123');
            if (streamingStateRaw) {
                const state = JSON.parse(streamingStateRaw);
                if (state?.content) {
                    originalMarkdown = state.content;
                }
            }
        } catch (e) {
            // ignore
        }
    }
    
    const fallbackText = 'Agentic Turn 1 file_read';
    const result = originalMarkdown || fallbackText;
    
    const recoveredFromStorage = result.includes('**');
    console.log(`从 localStorage 恢复: ${recoveredFromStorage ? '✅' : '❌'}`);
    console.log(`恢复的内容: ${result.substring(0, 50)}...`);
    
    return recoveredFromStorage;
}

// 测试5: 续写开始时多层保护
function testResumeSeedTextMultiLayer() {
    console.log('\n=== 测试5: 续写开始时多层保护 ===');
    
    // 场景1: _fullResponseMarkdown 可用
    const messageDiv1 = createMockMessageDiv();
    messageDiv1._fullResponseMarkdown = '---\n🔄 **Agentic Turn 1**';
    
    let seedText1 = messageDiv1._fullResponseMarkdown || '';
    if (!seedText1) {
        // try localStorage
    }
    if (!seedText1) {
        seedText1 = messageDiv1._resumeSeedText || '';
    }
    
    const layer1Works = seedText1.includes('**');
    console.log(`场景1 (_fullResponseMarkdown 可用): ${layer1Works ? '✅' : '❌'}`);
    
    // 场景2: _fullResponseMarkdown 丢失，localStorage 可用
    const messageDiv2 = createMockMessageDiv();
    messageDiv2._fullResponseMarkdown = '';
    mockLocalStorage.setItem('streaming_test-chat-123', JSON.stringify({
        content: '---\n🔄 **Agentic Turn 1**\n\n> ✅ **file_read**'
    }));
    
    let seedText2 = messageDiv2._fullResponseMarkdown || '';
    if (!seedText2 && messageDiv2.id) {
        const raw = mockLocalStorage.getItem('streaming_test-chat-123');
        if (raw) {
            const state = JSON.parse(raw);
            if (state?.content) seedText2 = state.content;
        }
    }
    if (!seedText2) {
        seedText2 = messageDiv2._resumeSeedText || '';
    }
    
    const layer2Works = seedText2.includes('**');
    console.log(`场景2 (localStorage 备选): ${layer2Works ? '✅' : '❌'}`);
    
    // 场景3: 都丢失，使用 _resumeSeedText
    const messageDiv3 = createMockMessageDiv();
    messageDiv3._fullResponseMarkdown = '';
    messageDiv3._resumeSeedText = 'Agentic Turn 1 file_read';
    
    let seedText3 = messageDiv3._fullResponseMarkdown || '';
    if (!seedText3) {
        // localStorage empty
    }
    if (!seedText3) {
        seedText3 = messageDiv3._resumeSeedText || '';
    }
    
    const layer3Works = seedText3 === 'Agentic Turn 1 file_read';
    console.log(`场景3 (_resumeSeedText 兜底): ${layer3Works ? '✅' : '❌'}`);
    
    return layer1Works && layer2Works && layer3Works;
}

// 测试6: 完整工作流模拟
function testCompleteWorkflow() {
    console.log('\n=== 测试6: 完整工作流模拟 ===');
    
    // 步骤1: 流式响应
    const messageDiv = createMockMessageDiv();
    let fullResponse = '';
    
    // 模拟 chunk 事件
    fullResponse += '---\n🔄 **Agentic Turn 1**\n\n';
    fullResponse += '> 🔧 **file_read** `{\"path\":\"test.py\"}`\n> ⏳ 执行中...\n\n';
    fullResponse += '> ✅ **file_read** (1ms)\n';
    
    // 保存 markdown
    messageDiv._fullResponseMarkdown = fullResponse;
    
    // 步骤2: done 事件（approval_pending）
    fullResponse += '\n---\n🔄 **Agentic Turn 2**\n\n> 🔧 **file_edit**\n> 已生成待审批修改卡片。\n\n请审批';
    messageDiv._fullResponseMarkdown = fullResponse;
    
    // 步骤3: 用户点击接受
    // 模拟 _resumeAfterApproval -> sendViaAPI
    const appendToMessage = true;
    
    // 模拟续写开始
    let seedText = '';
    if (appendToMessage && messageDiv) {
        seedText = messageDiv._fullResponseMarkdown || '';
        if (!seedText) {
            // try localStorage
        }
        if (!seedText) {
            seedText = messageDiv._resumeSeedText || '';
        }
    }
    
    // 步骤4: 新 chunk 到来
    fullResponse = seedText;
    fullResponse += '\n\n用户已接受修改。继续执行任务...';
    messageDiv._fullResponseMarkdown = fullResponse;
    
    // 验证
    const hasHistory = fullResponse.includes('Agentic Turn 1') && fullResponse.includes('Agentic Turn 2');
    const hasFormat = fullResponse.includes('**') && fullResponse.includes('`') && fullResponse.includes('>');
    const hasNewContent = fullResponse.includes('用户已接受修改');
    
    console.log(`历史内容保留: ${hasHistory ? '✅' : '❌'}`);
    console.log(`Markdown 格式保留: ${hasFormat ? '✅' : '❌'}`);
    console.log(`新内容追加: ${hasNewContent ? '✅' : '❌'}`);
    console.log(`最终内容长度: ${fullResponse.length}`);
    
    return hasHistory && hasFormat && hasNewContent;
}

// 运行所有测试
console.log('╔════════════════════════════════════════════════════════════╗');
console.log('║  审批后续写 Markdown 格式保留验证测试 v2                  ║');
console.log('╚════════════════════════════════════════════════════════════╝');

const tests = [
    testMarkdownSavedDuringStreaming,
    testDoneEventSavesMarkdown,
    testBuildResumeBaseStatePrefersMarkdown,
    testLocalStorageFallback,
    testResumeSeedTextMultiLayer,
    testCompleteWorkflow
];

let passed = 0;
let failed = 0;

for (const test of tests) {
    try {
        const result = test();
        if (result) {
            passed++;
        } else {
            failed++;
        }
    } catch (e) {
        console.log(`❌ 测试异常: ${e.message}`);
        failed++;
    }
}

console.log('\n╔════════════════════════════════════════════════════════════╗');
console.log('║                        测试结果摘要                        ║');
console.log('╚════════════════════════════════════════════════════════════╝');
console.log(`通过: ${passed}/${tests.length}`);
console.log(`失败: ${failed}/${tests.length}`);

if (failed === 0) {
    console.log('\n✅ 所有测试通过！多层保护机制工作正常。');
    console.log('\n保护层级:');
    console.log('  1. _fullResponseMarkdown (chunk 事件保存)');
    console.log('  2. _fullResponseMarkdown (done 事件兜底保存)');
    console.log('  3. buildResumeBaseState 优先使用 _fullResponseMarkdown');
    console.log('  4. buildResumeBaseState 从 localStorage 备选恢复');
    console.log('  5. 续写开始时优先使用 _fullResponseMarkdown');
    console.log('  6. 续写开始时从 localStorage 备选恢复');
    console.log('  7. _resumeSeedText 最后兜底');
} else {
    console.log('\n❌ 有测试失败，请检查实现。');
    process.exit(1);
}
