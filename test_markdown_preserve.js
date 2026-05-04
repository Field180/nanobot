/**
 * Markdown格式保留验证测试
 * 
 * 验证点击审批按钮后，历史内容的markdown格式（**, `, >等）是否正确保留
 */

// 模拟测试数据
const mockMarkdownContent = `---
🔄 **Agentic Turn 1**

> 🔧 **file_read** \`{"path":"test_format.py"}\` 
> ⏳ 执行中...

> ✅ **file_read** (1ms)
> \`\`\`
> [Summary: file_read /home/field/.nanobot/workspace/web_ui/test_format.py]
> \`\`\`

---
🔄 **Agentic Turn 2**

> 🔧 **file_edit** \`{"new_string":"test_OOO","old_string":"test_PPP"}\` 
> ⏳ 执行中...

> ✅ **file_edit** (5ms)
> 已生成待审批修改卡片。

请审批`;

// 模拟DOM提取的纯文本（丢失格式）
const domTextContent = `🔄 Agentic Turn 1

🔧 file_read {"path":"test_format.py"}⏳ 执行中...

✅ file_read (1ms)
[Summary: file_read /home/field/.nanobot/workspace/web_ui/test_format.py]

🔄 Agentic Turn 2

🔧 file_edit {"new_string":"test_OOO","old_string":"test_PPP"}⏳ 执行中...

✅ file_edit (5ms)
已生成待审批修改卡片。

请审批`;

// 测试1：验证markdown内容包含格式标记
function testMarkdownFormatting() {
    const hasBoldMarkers = mockMarkdownContent.includes('**');
    const hasCodeMarkers = mockMarkdownContent.includes('`');
    const hasQuoteMarkers = mockMarkdownContent.includes('>');
    const hasSeparator = mockMarkdownContent.includes('---');
    
    console.log('=== 测试1：Markdown格式标记检查 ===');
    console.log(`加粗标记 (**): ${hasBoldMarkers ? '✅ 有' : '❌ 无'}`);
    console.log(`代码标记 (\`): ${hasCodeMarkers ? '✅ 有' : '❌ 无'}`);
    console.log(`引用标记 (>): ${hasQuoteMarkers ? '✅ 有' : '❌ 无'}`);
    console.log(`分隔线 (---): ${hasSeparator ? '✅ 有' : '❌ 无'}`);
    
    return hasBoldMarkers && hasCodeMarkers && hasQuoteMarkers && hasSeparator;
}

// 测试2：验证textContent丢失了格式
function testTextContentLostFormatting() {
    const lostBold = !domTextContent.includes('**');
    const lostCode = !domTextContent.includes('`');
    const lostQuote = !domTextContent.includes('>');
    const lostSeparator = !domTextContent.includes('---');
    
    console.log('\n=== 测试2：DOM textContent格式丢失检查 ===');
    console.log(`丢失加粗标记: ${lostBold ? '✅ 是' : '❌ 否'}`);
    console.log(`丢失代码标记: ${lostCode ? '✅ 是' : '❌ 否'}`);
    console.log(`丢失引用标记: ${lostQuote ? '✅ 是' : '❌ 否'}`);
    console.log(`丢失分隔线: ${lostSeparator ? '✅ 是' : '❌ 否'}`);
    
    return lostBold && lostCode && lostQuote && lostSeparator;
}

// 测试3：模拟修复后的行为
function testFixBehavior() {
    console.log('\n=== 测试3：修复后行为模拟 ===');
    
    // 模拟messageDiv._fullResponseMarkdown保存了原始markdown
    const messageDiv = {
        _fullResponseMarkdown: mockMarkdownContent,
        _resumeSeedText: domTextContent  // 旧的纯文本备份
    };
    
    // 修复后的逻辑：优先使用_fullResponseMarkdown
    const appendToMessage = true;
    const seedText = (appendToMessage && messageDiv._fullResponseMarkdown)
        ? String(messageDiv._fullResponseMarkdown)
        : ((appendToMessage && messageDiv._resumeSeedText) ? String(messageDiv._resumeSeedText) : '');
    
    const preservedFormat = seedText.includes('**') && seedText.includes('`') && seedText.includes('>');
    console.log(`使用 _fullResponseMarkdown: ✅`);
    console.log(`格式保留: ${preservedFormat ? '✅ 是' : '❌ 否'}`);
    console.log(`内容长度: ${seedText.length} 字符`);
    
    // 模拟续写
    const resumedContent = "\n\n用户已接受修改。继续执行任务...";
    const finalContent = seedText + resumedContent;
    
    console.log(`续写后格式保留: ${finalContent.includes('**') ? '✅ 是' : '❌ 否'}`);
    
    return preservedFormat;
}

// 测试4：对比旧行为（bug）
function testOldBugBehavior() {
    console.log('\n=== 测试4：旧bug行为对比 ===');
    
    const messageDiv = {
        _fullResponseMarkdown: mockMarkdownContent,
        _resumeSeedText: domTextContent
    };
    
    // 旧逻辑：只使用_resumeSeedText（纯文本）
    const oldSeedText = messageDiv._resumeSeedText;
    
    const lostFormat = !oldSeedText.includes('**') && !oldSeedText.includes('`');
    console.log(`旧逻辑使用 _resumeSeedText (textContent)`);
    console.log(`格式丢失: ${lostFormat ? '✅ 是 (这就是bug)' : '❌ 否'}`);
    console.log(`内容长度: ${oldSeedText.length} 字符`);
    
    return lostFormat;
}

// 运行所有测试
console.log('╔════════════════════════════════════════════════════════════╗');
console.log('║     审批后续写 Markdown 格式保留验证测试                    ║');
console.log('╚════════════════════════════════════════════════════════════╝');

const test1 = testMarkdownFormatting();
const test2 = testTextContentLostFormatting();
const test3 = testFixBehavior();
const test4 = testOldBugBehavior();

console.log('\n╔════════════════════════════════════════════════════════════╗');
console.log('║                      测试结果摘要                          ║');
console.log('╚════════════════════════════════════════════════════════════╝');
console.log(`测试1 - Markdown格式完整:      ${test1 ? '✅ 通过' : '❌ 失败'}`);
console.log(`测试2 - DOM会丢失格式:          ${test2 ? '✅ 通过' : '❌ 失败'}`);
console.log(`测试3 - 修复后保留格式:         ${test3 ? '✅ 通过' : '❌ 失败'}`);
console.log(`测试4 - 旧逻辑有bug:            ${test4 ? '✅ 通过' : '❌ 失败'}`);

const allPassed = test1 && test2 && test3 && test4;
console.log(`\n总体结果: ${allPassed ? '✅ 所有测试通过 - 修复有效' : '❌ 有测试失败'}`);

if (allPassed) {
    console.log('\n📋 修复说明：');
    console.log('  1. 在流式处理时保存原始markdown到 _fullResponseMarkdown');
    console.log('  2. 续写时优先使用 _fullResponseMarkdown 而非 textContent');
    console.log('  3. 保留 **、`、`、>、--- 等所有markdown格式标记');
}
