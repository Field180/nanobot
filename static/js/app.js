/**
 * Nanobot Web UI - 前端应用逻辑
 * 与 Nanobot Gateway API 通信
 */

// 配置
const CONFIG = {
    API_URL: window.location.origin,
    DEFAULT_USER: 'web_user',
    MAX_HISTORY: 50,
    ENABLE_BACKEND_HISTORY: true,
    STREAMING_STALE_MS: 5 * 60 * 1000
};

const MARKDOWN_CACHE_MAX_ENTRIES = 240;
const MARKDOWN_DEBUG_STORAGE_KEY = 'nanobot_markdown_debug';

// 全局状态
let currentSession = null;
let messageHistory = [];
let wsConnection = null;
let isProcessing = false;
let processingStartTime = null;  // 记录处理开始时间，用于计算耗时
let currentChatId = Date.now().toString();
let activeStreamingChatId = null; // 跟踪当前正在流式响应的对话ID
let currentAbortController = null;
let stopRequested = false;
let batchModeEnabled = false;
let selectedChatIds = new Set();
let selectedAttachments = [];
let snnModeEnabled = false;  // SNN模式开关（脉冲神经思考）
let snnModeAEnabled = false;  // 深度脉冲A模式 - 默认关闭
let snnModeBEnabled = false;  // 深度脉冲B模式 - 默认关闭
let neuraCoreEnabled = false;  // NeuraCore神经核心模式
let spikeGPTModeEnabled = false;  // SpikeGPT融合模式 - 默认关闭
let selectedModel = 'qwen3.5:122b';  // 当前选择的模型 - 默认Qwen3.5 122B
let selectedBackend = 'ollama';  // 当前调用方式 - 默认Ollama API
// P1-1: Token status bar state
let sessionTotalTokens = 0;
let lastTurnTokens = 0;
let lastTurnSpeed = 0;
let lastTurnModel = '';
// P1-2: Sidebar task progress state
let sessionTaskMap = {}; // task_id → {id, parent_id, title, state}
const DEFAULT_GROUPS = ['未分组'];
let filePreviewObjectUrl = null;
const TEXT_PREVIEW_EXTENSIONS = new Set([
    'txt', 'md', 'json', 'csv', 'log', 'yaml', 'yml', 'xml', 'html', 'css', 'js', 'ts', 'jsx', 'tsx',
    'py', 'java', 'go', 'rb', 'php', 'rs', 'c', 'cpp', 'h', 'hpp', 'sql', 'ini', 'cfg', 'toml', 'sh', 'bat'
]);
const IMAGE_PREVIEW_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg']);
const ATTACHMENT_CACHE_KEY = 'nanobot_attachment_cache';

// Markdown 渲染链（渐进式向 llama.cpp 架构对齐）
const MARKDOWN_SANITIZE_OPTIONS = {
    ALLOWED_TAGS: [
        'div', 'span', 'pre', 'code', 'button', 'i', 'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'ul', 'ol', 'li', 'table', 'thead', 'tbody', 'tr', 'td', 'th', 'br', 'hr',
        'strong', 'em', 'blockquote', 'a', 'details', 'summary'
    ],
    ALLOWED_ATTR: [
        'class', 'id', 'data-code', 'data-code-id', 'data-action', 'type', 'href', 'target', 'title',
        'style', 'aria-label', 'aria-hidden', 'open'
    ]
};

const streamingMarkdownState = {
    pendingContent: null,
    rafId: null,
    lastRenderedContent: '',
    lastRenderedHtml: '',
    markdownRenderState: null,
    metrics: {
        frameRenders: 0,
        cachedFrameHits: 0
    }
};

let markdownInteractionsBound = false;
const markdownBlockCacheStateByContainer = new WeakMap();
const streamingLifecycleStateByContainer = new WeakMap();
const changeSetStateCache = new Map();
const fileChangeSummaryState = {
    sessionId: '',
    changeSets: [],
    loading: false,
    busy: false,
    expanded: false,
    error: '',
    lastUpdatedAt: 0,
};

function isMarkdownDebugEnabled() {
    try {
        if (typeof window !== 'undefined' && window.__NANOBOT_MARKDOWN_DEBUG__ === true) {
            return true;
        }
        return localStorage.getItem(MARKDOWN_DEBUG_STORAGE_KEY) === '1';
    } catch (error) {
        return false;
    }
}

function markdownDebugLog(...args) {
    if (!isMarkdownDebugEnabled()) return;
    console.log('[markdown-debug]', ...args);
}

function maybeReportMarkdownMetrics(renderState, source = 'unknown') {
    if (!renderState || !renderState.metrics) return;
    const metrics = renderState.metrics;
    if (!isMarkdownDebugEnabled()) return;
    if (metrics.renderPasses % 25 !== 0) return;

    const cacheLookups = metrics.cacheHits + metrics.cacheMisses;
    const hitRate = cacheLookups > 0 ? ((metrics.cacheHits / cacheLookups) * 100).toFixed(1) : '0.0';
    markdownDebugLog(
        `${source} metrics`,
        `passes=${metrics.renderPasses}`,
        `hitRate=${hitRate}%`,
        `hits=${metrics.cacheHits}`,
        `misses=${metrics.cacheMisses}`,
        `rerenders=${metrics.reRenderCount}`,
        `evictions=${metrics.evictions}`
    );
}

function createStreamingLifecycleState() {
    return {
        phase: 'idle',
        lastEvent: null,
        updatedAt: Date.now()
    };
}

function getStreamingLifecycleState(container) {
    if (!container) return null;
    if (!streamingLifecycleStateByContainer.has(container)) {
        streamingLifecycleStateByContainer.set(container, createStreamingLifecycleState());
    }
    return streamingLifecycleStateByContainer.get(container);
}

function resetStreamingLifecycleState(container) {
    if (!container) return;
    streamingLifecycleStateByContainer.set(container, createStreamingLifecycleState());
}

function transitionStreamingLifecycle(container, eventType) {
    const state = getStreamingLifecycleState(container);
    if (!state) return;

    const previousPhase = state.phase;
    if (eventType === 'start') {
        state.phase = 'started';
    } else if (
        eventType === 'chunk' ||
        eventType === 'llm_chunk' ||
        eventType === 'status' ||
        eventType === 'heartbeat' ||
        eventType === 'build' ||
        eventType === 'cognition' ||
        eventType === 'perception' ||
        eventType === 'tool_start' ||
        eventType === 'tool_result' ||
        eventType === 'agentic_turn'
    ) {
        state.phase = 'streaming';
    } else if (eventType === 'done' || eventType === 'complete') {
        state.phase = 'completed';
    } else if (eventType === 'error') {
        state.phase = 'error';
    }

    state.lastEvent = eventType;
    state.updatedAt = Date.now();

    if (previousPhase !== state.phase) {
        markdownDebugLog(`stream lifecycle ${previousPhase} -> ${state.phase} via ${eventType}`);
    }
}

function getStreamingStatusColor(eventType) {
    if (eventType === 'cognition') return '#10b981';
    if (eventType === 'error') return '#ef4444';
    if (eventType === 'mode') return '#06b6d4';
    return '#8b5cf6';
}

function getHeartbeatStatusText(data) {
    const elapsed = Number(data && data.elapsed);
    if (Number.isFinite(elapsed) && elapsed >= 0) {
        return `⏳ 已思考 ${Math.floor(elapsed)} 秒...`;
    }
    const turn = Number(data && data.turn);
    if (Number.isFinite(turn) && turn > 0) {
        return `⏳ 第 ${turn} 轮处理中...`;
    }
    const status = data && typeof data.status === 'string' ? data.status.trim() : '';
    if (status && status !== 'alive') {
        return `⏳ ${status}...`;
    }
    return '⏳ 处理中...';
}

function buildHeartbeatContent(fullResponse, data) {
    const heartbeatText = getHeartbeatStatusText(data);
    return fullResponse ? `${fullResponse}\n\n---\n${heartbeatText}` : heartbeatText;
}

function getStreamingContentFallbackText(streamingContent) {
    const text = streamingContent ? String(streamingContent.textContent || '').trim() : '';
    if (!text) return '';
    if (text === '🤔 思考中...') return '';
    if (text.startsWith('⏳ ')) return '';
    return text;
}

function getStreamingStatusText(eventType, data, fallbackText = '') {
    if (fallbackText) return fallbackText;
    if (eventType === 'start') return '🤔 思考中...';
    if (eventType === 'heartbeat') {
        return getHeartbeatStatusText(data);
    }
    if (data && data.message) return String(data.message);
    return '处理中...';
}

function renderStreamingStatusFrame(streamingDiv, eventType, data, options = {}) {
    if (!streamingDiv) return;
    transitionStreamingLifecycle(streamingDiv, eventType);

    const {
        icon = 'fa-robot',
        messageStyle = '',
        markdownContent = '',
        statusPrefix = '',
        fallbackStatusText = ''
    } = options;

    if (eventType === 'start') {
        resetMarkdownBlockCacheState(streamingDiv);
    }

    const statusTextRaw = getStreamingStatusText(eventType, data, fallbackStatusText);
    const statusText = statusPrefix ? `${statusPrefix}${statusTextRaw}` : statusTextRaw;
    const statusColor = getStreamingStatusColor(eventType);

    renderStreamingFrame(streamingDiv, {
        icon,
        messageStyle,
        statusText,
        statusColor,
        markdownContent
    });
}

function createMarkdownBlockCacheState() {
    return {
        previousContent: '',
        stableTokenKeys: [],
        stableHtmlCache: new Map(),
        maxEntries: MARKDOWN_CACHE_MAX_ENTRIES,
        metrics: {
            cacheHits: 0,
            cacheMisses: 0,
            tokenRenders: 0,
            evictions: 0,
            reRenderCount: 0,
            renderPasses: 0
        }
    };
}

function getMarkdownBlockCacheState(container) {
    if (!container) return null;
    if (!markdownBlockCacheStateByContainer.has(container)) {
        markdownBlockCacheStateByContainer.set(container, createMarkdownBlockCacheState());
    }
    return markdownBlockCacheStateByContainer.get(container);
}

function resetMarkdownBlockCacheState(container) {
    if (!container) return;
    markdownBlockCacheStateByContainer.set(container, createMarkdownBlockCacheState());
}

function getMarkdownTokenKey(token, fallbackIndex = 0) {
    if (!token || typeof token !== 'object') {
        return `unknown-${fallbackIndex}`;
    }

    const raw = typeof token.raw === 'string' ? token.raw : '';
    return `${token.type || 'unknown'}:${raw || `idx-${fallbackIndex}`}`;
}

function renderMarkdownToken(token) {
    if (!token) return '';

    if (typeof token.raw === 'string' && token.raw.length > 0) {
        return marked.parse(token.raw);
    }

    try {
        return marked.parser([token]);
    } catch (error) {
        return '';
    }
}

function renderMarkdownWithBlockCache(markdownText, renderState) {
    if (!renderState) {
        return parseMarkdownToHtml(markdownText);
    }

    const metrics = renderState.metrics;
    if (metrics) {
        metrics.renderPasses += 1;
        metrics.reRenderCount += 1;
    }

    const text = markdownText || '';
    const tokens = marked.lexer(text);
    if (!tokens || tokens.length === 0) {
        renderState.previousContent = text;
        renderState.stableTokenKeys = [];
        return '';
    }

    const stableCount = Math.max(tokens.length - 1, 0);
    const appendMode =
        renderState.previousContent.length > 0 && text.startsWith(renderState.previousContent);
    const htmlParts = [];
    const nextStableKeys = [];

    for (let i = 0; i < stableCount; i++) {
        const token = tokens[i];
        const tokenKey = getMarkdownTokenKey(token, i);
        nextStableKeys.push(tokenKey);

        const canReuse =
            appendMode &&
            renderState.stableTokenKeys[i] === tokenKey &&
            renderState.stableHtmlCache.has(tokenKey);

        if (canReuse) {
            const cachedHtml = renderState.stableHtmlCache.get(tokenKey);
            renderState.stableHtmlCache.delete(tokenKey);
            renderState.stableHtmlCache.set(tokenKey, cachedHtml);
            if (metrics) metrics.cacheHits += 1;
            htmlParts.push(cachedHtml);
            continue;
        }

        if (metrics) metrics.cacheMisses += 1;

        const tokenHtml = renderMarkdownToken(token);
        if (metrics) metrics.tokenRenders += 1;
        if (renderState.stableHtmlCache.has(tokenKey)) {
            renderState.stableHtmlCache.delete(tokenKey);
        }
        renderState.stableHtmlCache.set(tokenKey, tokenHtml);

        if (renderState.stableHtmlCache.size > renderState.maxEntries) {
            const oldestKey = renderState.stableHtmlCache.keys().next().value;
            if (oldestKey !== undefined) {
                renderState.stableHtmlCache.delete(oldestKey);
                if (metrics) metrics.evictions += 1;
            }
        }

        htmlParts.push(tokenHtml);
    }

    const unstableToken = tokens[tokens.length - 1];
    const unstableHtml = unstableToken ? renderMarkdownToken(unstableToken) : '';

    renderState.previousContent = text;
    renderState.stableTokenKeys = nextStableKeys;

    maybeReportMarkdownMetrics(renderState, 'block-cache');

    return htmlParts.join('') + unstableHtml;
}

// 初始化
document.addEventListener('DOMContentLoaded', () => {
    initApp();
    bindMarkdownInteractionHandlers();
    document.addEventListener('click', closeHistoryMenuOnOutsideClick);
    initChatSearch();
    initFavoritesPanel();
    initInputToolbar();
    initFilePreview();
    // P3: Pre-fetch skills list and close menu on outside click
    _fetchSkills();
    document.addEventListener('click', (e) => {
        if (_slashState.visible) {
            const menu = document.getElementById('slashMenu');
            const input = document.getElementById('messageInput');
            if (menu && !menu.contains(e.target) && e.target !== input) {
                _slashHide();
            }
        }
    });
});

// 初始化
async function initApp() {
    console.log('[initApp] Starting initialization...');
    
    // 初始化输入框高度
    const messageInput = document.getElementById('messageInput');
    if (messageInput) {
        autoResize(messageInput);
    }
    
    // 更新SNN按钮状态（深度脉冲A默认开启）
    updateAllSNNButtons();
    
    // 检查后端连接
    await checkConnection();

    // 预热当前会话快照，尽可能在首个 task_update 到来前拿到 task_id
    await preloadCurrentSessionSnapshot();
    
    // 清理重复历史记录
    deduplicateHistory();
    await deepCleanAllChatMessages();
    
    // 尝试加载最近的聊天记录
    console.log('[initApp] Calling loadMostRecentChat...');
    const loaded = await loadMostRecentChat();
    console.log('[initApp] loadMostRecentChat returned:', loaded);
    
    // 如果没有加载到历史记录，初始化新会话
    if (!loaded) {
        console.log('[initApp] No chat loaded, showing welcome message');
        await initSession();
        showWelcomeMessage();
    }

    updateHomeGreeting();
    
    // 加载历史记录列表到侧边栏
    loadChatHistory();

    // 根据当前流式状态更新按钮
    updateSendButton();

    // 网关重启/断线恢复：与后端对齐流式状态，避免本地残留 streaming_ 导致误显示“停止”
    try {
        await reconcileStreamingState(currentChatId);
    } catch (e) {
        console.warn('[initApp] reconcileStreamingState failed:', e);
    }
    
    // 聚焦输入框
    document.getElementById('messageInput').focus();
    
    console.log('[initApp] Initialization complete');
}

async function reconcileStreamingState(chatId) {
    if (!chatId) return;
    const url = `${CONFIG.API_URL}/api/sessions/${encodeURIComponent(chatId)}/stream_state`;
    let data = null;
    try {
        const response = await fetch(url, { method: 'GET', headers: { 'Accept': 'application/json' } });
        if (!response.ok) {
            // 404/500 都按“后端无流式任务”处理
            localStorage.removeItem(`streaming_${chatId}`);
            updateSendButton();
            return;
        }
        data = await response.json();
    } catch (e) {
        // 网络错误时不贸然清理（避免误杀真实在跑的状态），交给轮询机制/用户操作
        throw e;
    }

    const streamState = data ? data.stream_state : null;
    const backendStatus = streamState && streamState.status ? String(streamState.status) : '';
    const backendIsStreaming = backendStatus === 'streaming' || backendStatus === 'stopping';

    if (!backendIsStreaming) {
        // 后端已不在流式：清理本地 streaming_ 标记，恢复按钮
        localStorage.removeItem(`streaming_${chatId}`);
        if (currentChatId === chatId) {
            const streamingDiv = document.getElementById('streamingMessage');
            if (streamingDiv) {
                streamingDiv.classList.remove('streaming');
                streamingDiv.removeAttribute('id');
            }
        }
        updateSendButton();
        return;
    }

    // 后端仍在流式：如果本地没有标记，补上并开启轮询
    const existing = localStorage.getItem(`streaming_${chatId}`);
    if (!existing) {
        localStorage.setItem(`streaming_${chatId}`, JSON.stringify({
            isStreaming: true,
            content: (streamState && streamState.content) ? String(streamState.content) : '',
            timestamp: Date.now()
        }));
    }
    if (currentChatId === chatId) {
        updateSendButton();
        startStreamingPoll(chatId);
    }
}

function formatFileSize(bytes) {
    if (!bytes && bytes !== 0) return '';
    if (bytes < 1024) return `${bytes}B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(2)}KB`;
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(2)}MB`;
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)}GB`;
}

function getFileExtension(name = '') {
    const parts = name.split('.');
    return parts.length > 1 ? parts.pop().toLowerCase() : '';
}

function isTextPreviewable(type = '', name = '') {
    if (type.startsWith('text/')) return true;
    const ext = getFileExtension(name);
    return TEXT_PREVIEW_EXTENSIONS.has(ext);
}

function isImagePreviewable(type = '', name = '') {
    if (type.startsWith('image/')) return true;
    const ext = getFileExtension(name);
    return IMAGE_PREVIEW_EXTENSIONS.has(ext);
}

function encodeAttachmentContent(content) {
    return content ? encodeURIComponent(content) : '';
}

function decodeAttachmentContent(content) {
    return content ? decodeURIComponent(content) : '';
}

function fileToDataUrl(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(reader.error || new Error('Failed to read file'));
        reader.readAsDataURL(file);
    });
}

function getAttachmentCacheKey({ name, size, url }) {
    return `${name || ''}|${size || ''}|${url || ''}`;
}

function cacheAttachmentContent({ name, size, url, content }) {
    if (!content) return;
    const cache = JSON.parse(localStorage.getItem(ATTACHMENT_CACHE_KEY) || '{}');
    const key = getAttachmentCacheKey({ name, size, url });
    cache[key] = content;
    localStorage.setItem(ATTACHMENT_CACHE_KEY, JSON.stringify(cache));
}

function getCachedAttachmentContent({ name, size, url }) {
    const cache = JSON.parse(localStorage.getItem(ATTACHMENT_CACHE_KEY) || '{}');
    const key = getAttachmentCacheKey({ name, size, url });
    return cache[key] || '';
}

function findStoredAttachmentContent({ name, size, url }) {
    const candidates = Array.isArray(messageHistory) ? messageHistory : [];
    for (let i = candidates.length - 1; i >= 0; i--) {
        const attachments = candidates[i]?.attachments || [];
        for (const attachment of attachments) {
            if (!attachment) continue;
            const sameName = name && attachment.name === name;
            const sameSize = size && attachment.size === size;
            const sameUrl = url && attachment.url === url;
            if ((sameName && sameUrl) || (sameName && sameSize)) {
                if (attachment.content) return attachment.content;
            }
        }
    }
    return getCachedAttachmentContent({ name, size, url });
}

function hydrateMessageAttachments(message) {
    if (!message || !Array.isArray(message.attachments)) return message;
    const attachments = message.attachments.map((attachment) => {
        if (!attachment || typeof attachment !== 'object') return attachment;
        const hydrated = { ...attachment };
        if (!hydrated.url && hydrated.dataUrl) {
            hydrated.url = hydrated.dataUrl;
        }
        if (!hydrated.content && isTextPreviewable(hydrated.type || '', hydrated.name || '')) {
            const cached = findStoredAttachmentContent(hydrated);
            if (cached) {
                hydrated.content = cached;
            }
        }
        return hydrated;
    });
    return { ...message, attachments };
}

function hydrateHistoryAttachments(history) {
    if (!Array.isArray(history)) return [];
    return history.map(hydrateMessageAttachments);
}

function cloneChangeSetList(changeSets = []) {
    if (!Array.isArray(changeSets)) return [];
    return changeSets
        .filter((item) => item && typeof item === 'object')
        .map((item) => {
            const cloned = { ...item };
            if (Array.isArray(item.files)) {
                cloned.files = item.files.map((file) => ({ ...file }));
            }
            return cloned;
        });
}

function getRenderableChangeSetCount(changeSets = []) {
    if (!Array.isArray(changeSets)) return 0;
    return changeSets.filter((changeSet) => typeof changeSet?.id === 'string' && changeSet.id.trim()).length;
}

function getPendingChangeSetCount(changeSets = []) {
    if (!Array.isArray(changeSets)) return 0;
    return changeSets.filter((changeSet) => {
        const status = changeSet?.status || 'pending';
        return status === 'pending';
    }).length;
}

function scoreChangeSetPayload(changeSets = []) {
    const total = Array.isArray(changeSets) ? changeSets.length : 0;
    const renderable = getRenderableChangeSetCount(changeSets);
    const pending = getPendingChangeSetCount(changeSets);
    return (renderable * 1000) + (pending * 100) + total;
}

function getMessageRichnessScore(message = {}) {
    let score = 0;
    if (Array.isArray(message.attachments) && message.attachments.length > 0) score += message.attachments.length * 100;
    if (message.elapsed_ms) score += 25;
    if (message.llama_stats) score += 25;
    if (message.final_answer) score += 40;
    if (message.thinking_trace) score += 20;
    if (message.agentic_transcript) score += Math.min(String(message.agentic_transcript).length, 4000) / 40;
    if (message.full_response_markdown) score += Math.min(String(message.full_response_markdown).length, 4000) / 40;
    score += scoreChangeSetPayload(message.change_sets || []);
    score += String(message.content || '').length;
    return score;
}

function mergeHistoryPreferLocal(localHistory, backendHistory) {
    const merged = Array.isArray(localHistory) ? localHistory.map((item) => ({ ...item })) : [];
    const backend = Array.isArray(backendHistory) ? backendHistory : [];

    if (backend.length === 0) {
        return merged;
    }

    const maxLen = Math.max(merged.length, backend.length);
    for (let i = 0; i < maxLen; i++) {
        const localMsg = merged[i];
        const backendMsg = backend[i];
        if (!backendMsg) continue;

        if (!localMsg) {
            merged.push({ ...backendMsg });
            continue;
        }

        if (localMsg.role !== backendMsg.role) {
            continue;
        }

        if (localMsg.role === 'assistant') {
            const localScore = getMessageRichnessScore(localMsg);
            const backendScore = getMessageRichnessScore(backendMsg);
            if (backendScore > localScore && backendMsg.content) {
                localMsg.content = String(backendMsg.content || '');
            }
            if (!localMsg.agentic_transcript && backendMsg.agentic_transcript) {
                localMsg.agentic_transcript = backendMsg.agentic_transcript;
            }
            if (!localMsg.full_response_markdown && backendMsg.full_response_markdown) {
                localMsg.full_response_markdown = backendMsg.full_response_markdown;
            }
            if (!localMsg.final_answer && backendMsg.final_answer) {
                localMsg.final_answer = { ...backendMsg.final_answer };
            }
            if (!localMsg.thinking_trace && backendMsg.thinking_trace) {
                localMsg.thinking_trace = backendMsg.thinking_trace;
            }
            if (!localMsg.elapsed_ms && backendMsg.elapsed_ms) {
                localMsg.elapsed_ms = backendMsg.elapsed_ms;
            }
            if (!localMsg.llama_stats && backendMsg.llama_stats) {
                localMsg.llama_stats = { ...backendMsg.llama_stats };
            }
            if ((Array.isArray(backendMsg.change_sets) && backendMsg.change_sets.length > (localMsg.change_sets || []).length)) {
                localMsg.change_sets = cloneChangeSetList(backendMsg.change_sets || []);
            }
            if ((Array.isArray(backendMsg.pending_change_set_ids) && backendMsg.pending_change_set_ids.length > (localMsg.pending_change_set_ids || []).length)) {
                localMsg.pending_change_set_ids = [...backendMsg.pending_change_set_ids];
            }
            if (!localMsg.approval_pending && backendMsg.approval_pending) {
                localMsg.approval_pending = true;
            }
        } else if (localMsg.role === 'user') {
            if ((!Array.isArray(localMsg.attachments) || localMsg.attachments.length === 0) && Array.isArray(backendMsg.attachments) && backendMsg.attachments.length > 0) {
                localMsg.attachments = backendMsg.attachments.map((item) => ({ ...item }));
            }
        }

        merged[i] = localMsg;
    }

    return normalizeChatHistory(merged);
}

function mergeLatestAssistantMetadata(baseHistory, fallbackHistory) {
    const merged = Array.isArray(baseHistory) ? baseHistory.map((item) => ({ ...item })) : [];
    const fallback = Array.isArray(fallbackHistory) ? fallbackHistory : [];
    let baseIndex = -1;
    let fallbackIndex = -1;
    for (let i = merged.length - 1; i >= 0; i--) {
        if (merged[i]?.role === 'assistant') {
            baseIndex = i;
            break;
        }
    }
    for (let i = fallback.length - 1; i >= 0; i--) {
        if (fallback[i]?.role === 'assistant') {
            fallbackIndex = i;
            break;
        }
    }
    if (baseIndex < 0 || fallbackIndex < 0) return merged;
    const baseMsg = merged[baseIndex] || {};
    const fallbackMsg = fallback[fallbackIndex] || {};
    const baseScore = getMessageRichnessScore(baseMsg);
    const fallbackScore = getMessageRichnessScore(fallbackMsg);
    const useFallbackContent = fallbackScore > baseScore;
    if (useFallbackContent) {
        baseMsg.content = String(fallbackMsg.content || '');
    }
    if (!baseMsg.agentic_transcript && fallbackMsg.agentic_transcript) {
        baseMsg.agentic_transcript = fallbackMsg.agentic_transcript;
    }
    if (!baseMsg.full_response_markdown && fallbackMsg.full_response_markdown) {
        baseMsg.full_response_markdown = fallbackMsg.full_response_markdown;
    }
    if (!baseMsg.final_answer && fallbackMsg.final_answer) {
        baseMsg.final_answer = { ...fallbackMsg.final_answer };
    }
    if (!baseMsg.thinking_trace && fallbackMsg.thinking_trace) {
        baseMsg.thinking_trace = fallbackMsg.thinking_trace;
    }
    if (!baseMsg.elapsed_ms && fallbackMsg.elapsed_ms) {
        baseMsg.elapsed_ms = fallbackMsg.elapsed_ms;
    }
    if (!baseMsg.llama_stats && fallbackMsg.llama_stats) {
        baseMsg.llama_stats = { ...fallbackMsg.llama_stats };
    }
    if ((fallbackMsg.change_sets || []).length > (baseMsg.change_sets || []).length) {
        baseMsg.change_sets = cloneChangeSetList(fallbackMsg.change_sets || []);
    }
    if ((fallbackMsg.pending_change_set_ids || []).length > (baseMsg.pending_change_set_ids || []).length) {
        baseMsg.pending_change_set_ids = [...(fallbackMsg.pending_change_set_ids || [])];
    }
    if (!baseMsg.approval_pending && fallbackMsg.approval_pending) {
        baseMsg.approval_pending = true;
    }
    if (!useFallbackContent && fallbackMsg.content && String(fallbackMsg.content).length > String(baseMsg.content || '').length) {
        baseMsg.content = String(fallbackMsg.content);
    }
    merged[baseIndex] = baseMsg;
    return merged;
}

function initFilePreview() {
    const modal = document.getElementById('filePreviewModal');
    const closeBtn = document.getElementById('filePreviewClose');
    if (!modal) return;
    const close = () => closeFilePreview();
    if (closeBtn) {
        closeBtn.addEventListener('click', close);
    }
    modal.addEventListener('click', (event) => {
        if (event.target === modal) {
            close();
        }
    });
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            close();
        }
    });
}

function bindAttachmentPreview(container, allowInputAttachment = false) {
    if (!container) return;
    const selector = allowInputAttachment
        ? '.attachment-item'
        : '.message-attachment-file, .message-attachment-image, [data-attachment-url]';
    container.querySelectorAll(selector).forEach((element) => {
        if (element.dataset.previewBound === 'true') return;
        element.addEventListener('click', (event) => {
            if (event.target.closest('.attachment-remove')) return;
            const name = element.dataset.attachmentName || '附件';
            const type = element.dataset.attachmentType || '';
            const url = element.dataset.attachmentUrl || '';
            const size = element.dataset.attachmentSize ? Number(element.dataset.attachmentSize) : null;
            let content = decodeAttachmentContent(element.dataset.attachmentContent || '');
            const attachment = selectedAttachments.find((item) => item.id === element.dataset.attachmentId) || null;
            if (!content) {
                content = findStoredAttachmentContent({ name, size, url });
            }
            openFilePreview({ name, type, url, size, file: attachment?.file || null, content });
        });
        element.dataset.previewBound = 'true';
    });
}

function closeFilePreview() {
    const modal = document.getElementById('filePreviewModal');
    const body = document.getElementById('filePreviewBody');
    const download = document.getElementById('filePreviewDownload');
    if (modal) {
        modal.classList.remove('active');
    }
    if (body) {
        body.innerHTML = '<div class="file-preview-placeholder"><div>选择文件以查看内容</div></div>';
    }
    if (download) {
        download.href = '#';
    }
    if (filePreviewObjectUrl) {
        URL.revokeObjectURL(filePreviewObjectUrl);
        filePreviewObjectUrl = null;
    }
}

async function openFilePreview({ name, type, url, size, file, content }) {
    const modal = document.getElementById('filePreviewModal');
    const title = document.getElementById('filePreviewName');
    const meta = document.getElementById('filePreviewMeta');
    const body = document.getElementById('filePreviewBody');
    const download = document.getElementById('filePreviewDownload');
    if (!modal || !body) return;

    if (filePreviewObjectUrl) {
        URL.revokeObjectURL(filePreviewObjectUrl);
        filePreviewObjectUrl = null;
    }

    const ext = getFileExtension(name);
    const sizeText = formatFileSize(size);
    const typeLabel = type ? type.split('/')[1]?.toUpperCase() : (ext ? ext.toUpperCase() : 'FILE');
    if (title) title.textContent = name || '文件预览';
    if (meta) meta.textContent = [typeLabel, sizeText].filter(Boolean).join(' · ');

    let downloadUrl = url || (file ? URL.createObjectURL(file) : '');
    if (downloadUrl && downloadUrl.startsWith('/')) {
        downloadUrl = `${CONFIG.API_URL}${downloadUrl}`;
    }
    if (download) {
        download.href = downloadUrl || '#';
    }
    if (file && !url) {
        filePreviewObjectUrl = downloadUrl;
    }

    modal.classList.add('active');
    body.innerHTML = '<div class="file-preview-placeholder"><div>正在加载文件内容...</div></div>';

    if (isImagePreviewable(type, name) && downloadUrl) {
        body.innerHTML = `<img src="${downloadUrl}" alt="${name || 'image'}" style="max-width: 100%; border-radius: 16px; box-shadow: 0 12px 30px rgba(15, 23, 42, 0.4);" />`;
        return;
    }

    if (!isTextPreviewable(type, name)) {
        body.innerHTML = `<div class="file-preview-placeholder">
            <div>该文件暂不支持在线预览</div>
            ${downloadUrl ? `<a href="${downloadUrl}" target="_blank">下载后查看</a>` : ''}
        </div>`;
        return;
    }

    try {
        let textContent = '';
        if (content) {
            textContent = content;
        } else if (file) {
            textContent = await file.text();
        } else if (downloadUrl) {
            const response = await fetch(downloadUrl);
            if (!response.ok) {
                throw new Error(`fetch failed: ${response.status}`);
            }
            textContent = await response.text();
        }
        const trimmed = textContent.trim();
        body.textContent = trimmed ? trimmed : '暂无内容';
    } catch (error) {
        console.error('[filePreview] failed:', error);
        body.innerHTML = `<div class="file-preview-placeholder">
            <div>文件读取失败</div>
            ${downloadUrl ? `<a href="${downloadUrl}" target="_blank">下载后查看</a>` : ''}
        </div>`;
    }
}

function addAttachment(file) {
    const id = `${file.name}-${file.size}-${file.lastModified}`;
    if (selectedAttachments.find((item) => item.id === id)) return;
    const attachment = {
        id,
        file,
        name: file.name,
        type: file.type,
        url: null,
        previewUrl: null,
        uploading: true,
        uploadPromise: null
    };
    if (file.type.startsWith('image/')) {
        attachment.previewUrl = URL.createObjectURL(file);
    }
    selectedAttachments.push(attachment);
    attachment.uploadPromise = uploadAttachment(attachment);
    renderAttachments();
}

function renderAttachments() {
    const container = document.getElementById('inputAttachments');
    if (!container) return;
    container.innerHTML = '';
    selectedAttachments.forEach((attachment) => {
        const item = document.createElement('div');
        item.className = `attachment-item${attachment.uploading ? ' uploading' : ''}`;
        item.dataset.attachmentName = attachment.name || '附件';
        item.dataset.attachmentType = attachment.type || '';
        item.dataset.attachmentUrl = attachment.url || attachment.previewUrl || '';
        item.dataset.attachmentSize = attachment.file?.size || '';
        item.dataset.attachmentId = attachment.id;
        if (attachment.content) {
            item.dataset.attachmentContent = encodeAttachmentContent(attachment.content);
        }
        const thumb = document.createElement('div');
        thumb.className = 'attachment-thumb';
        if (attachment.previewUrl) {
            const img = document.createElement('img');
            img.src = attachment.previewUrl;
            img.alt = attachment.name;
            thumb.appendChild(img);
            const preview = document.createElement('div');
            preview.className = 'attachment-preview';
            const previewImg = document.createElement('img');
            previewImg.src = attachment.previewUrl;
            previewImg.alt = attachment.name;
            preview.appendChild(previewImg);
            thumb.appendChild(preview);
        } else {
            thumb.innerHTML = '<i class="fas fa-file"></i>';
        }
        const label = document.createElement('span');
        label.textContent = attachment.uploading ? `${attachment.name}（上传中）` : attachment.name;
        const remove = document.createElement('button');
        remove.className = 'attachment-remove';
        remove.type = 'button';
        remove.innerHTML = '<i class="fas fa-times"></i>';
        remove.addEventListener('click', () => {
            removeAttachment(attachment.id);
        });
        item.appendChild(thumb);
        item.appendChild(label);
        if (attachment.uploading) {
            const progress = document.createElement('div');
            progress.className = 'attachment-progress';
            item.appendChild(progress);
        }
        item.appendChild(remove);
        container.appendChild(item);
    });
    const wrapper = document.querySelector('[data-scroll="input"]');
    setupAttachmentScroll(wrapper);
    bindAttachmentPreview(container, true);
}

function setupAttachmentScroll(wrapper) {
    if (!wrapper || wrapper.dataset.bound === 'true') return;
    const scroller = wrapper.querySelector('.input-attachments, .message-attachments');
    const leftBtn = wrapper.querySelector('[data-scroll-left]');
    const rightBtn = wrapper.querySelector('[data-scroll-right]');
    if (!scroller || !leftBtn || !rightBtn) return;

    const updateButtons = () => {
        const maxScroll = scroller.scrollWidth - scroller.clientWidth;
        leftBtn.disabled = scroller.scrollLeft <= 0;
        rightBtn.disabled = scroller.scrollLeft >= maxScroll - 1;
    };

    leftBtn.addEventListener('click', () => {
        scroller.scrollBy({ left: -140, behavior: 'smooth' });
    });
    rightBtn.addEventListener('click', () => {
        scroller.scrollBy({ left: 140, behavior: 'smooth' });
    });
    scroller.addEventListener('scroll', updateButtons);
    window.requestAnimationFrame(updateButtons);
    wrapper.dataset.bound = 'true';
}

function removeAttachment(id) {
    const index = selectedAttachments.findIndex((item) => item.id === id);
    if (index === -1) return;
    const [removed] = selectedAttachments.splice(index, 1);
    if (removed?.previewUrl) {
        URL.revokeObjectURL(removed.previewUrl);
    }
    renderAttachments();
}

async function uploadAttachment(attachment) {
    if (!attachment?.file) return;
    const formData = new FormData();
    formData.append('file', attachment.file);
    const sessionId = currentSession || currentChatId;
    if (sessionId) {
        formData.append('session_id', sessionId);
    }
    try {
        const response = await fetch(`${CONFIG.API_URL}/api/upload`, {
            method: 'POST',
            body: formData
        });
        if (!response.ok) {
            throw new Error(`上传失败: ${response.status}`);
        }
        const data = await response.json();
        if (data?.success && data.url) {
            attachment.url = data.url.startsWith('http')
                ? data.url
                : `${CONFIG.API_URL}${data.url}`;
        }
    } catch (error) {
        console.error('Upload failed:', error);
    } finally {
        attachment.uploading = false;
        renderAttachments();
    }
}

function getUserDisplayName() {
    const stored = localStorage.getItem('nanobot_user_name');
    if (stored && stored.trim()) return stored.trim();
    return CONFIG.DEFAULT_USER || 'Nanobot';
}

function updateHomeGreeting() {
    const greeting = document.getElementById('homeGreeting');
    if (!greeting) return;
    const name = getUserDisplayName();
    greeting.textContent = `Hi，${name}`;
}

function getOrderedHistory(history) {
    return [...history]
        .map(item => ({
            ...item,
            pinned: Boolean(item.pinned),
            pinnedAt: item.pinnedAt || 0,
            group: item.group || '未分组'
        }))
        .sort((a, b) => {
            if (a.pinned && b.pinned) {
                return (b.pinnedAt || b.timestamp) - (a.pinnedAt || a.timestamp);
            }
            if (a.pinned) return -1;
            if (b.pinned) return 1;
            return b.timestamp - a.timestamp;
        });
}

function normalizeHistoryGroups(history) {
    const groups = {};
    const ordered = getOrderedHistory(history);
    ordered.forEach(item => {
        const groupName = item.pinned ? '置顶' : (item.group || '未分组');
        if (!groups[groupName]) {
            groups[groupName] = [];
        }
        groups[groupName].push(item);
    });
    DEFAULT_GROUPS.forEach(groupName => {
        if (!groups[groupName]) {
            groups[groupName] = [];
        }
    });
    if (!groups['置顶']) {
        delete groups['置顶'];
    }
    return groups;
}

function getChatMeta(chatId) {
    const history = JSON.parse(localStorage.getItem('nanobot_chats') || '[]');
    return history.find(item => item.id === chatId);
}

function normalizeChatHistory(history) {
    if (!Array.isArray(history)) return [];
    // Pass 1: Remove consecutive same-role same-content messages
    const pass1 = [];
    history.forEach((entry) => {
        const prev = pass1[pass1.length - 1];
        if (
            prev
            && prev.role === entry?.role
            && prev.content === entry?.content
        ) {
            const prevScore = getMessageRichnessScore(prev);
            const entryScore = getMessageRichnessScore(entry);
            if (entryScore > prevScore) {
                pass1[pass1.length - 1] = entry;
            }
            return;
        }
        pass1.push(entry);
    });
    // Pass 2: Remove blocks of 2+ messages that repeat any earlier sequence
    // e.g. [A,B,C,D, A,B,C,D, A,B] → [A,B,C,D]
    const pass2 = [];
    let i = 0;
    while (i < pass1.length) {
        let bestSkip = 0;
        // Check all starting positions in pass2 (within a window of 20)
        const windowStart = Math.max(0, pass2.length - 20);
        for (let start = windowStart; start < pass2.length; start++) {
            const maxLen = Math.min(pass2.length - start, pass1.length - i);
            if (maxLen < 2) continue;
            let matchLen = 0;
            for (let k = 0; k < maxLen; k++) {
                if (pass1[i + k].role === pass2[start + k].role &&
                    pass1[i + k].content === pass2[start + k].content) {
                    matchLen++;
                } else break;
            }
            if (matchLen >= 2 && matchLen > bestSkip) bestSkip = matchLen;
        }
        if (bestSkip >= 2) {
            i += bestSkip; // skip the duplicate block
        } else {
            pass2.push(pass1[i]);
            i++;
        }
    }
    // Pass 3: Deduplicate user messages with identical content that appear
    // multiple times, keeping the pair with the longest assistant response
    const pass3 = [];
    const seenUserPairs = new Map(); // content → index in pass3 of the user msg
    for (let j = 0; j < pass2.length; j++) {
        const cur = pass2[j];
        if (cur.role === 'user' && seenUserPairs.has(cur.content)) {
            const prevIdx = seenUserPairs.get(cur.content);
            const prevAssistant = pass3[prevIdx + 1];
            const nextAssistant = pass2[j + 1];
            // If next message is also assistant, this is a duplicate user+assistant pair
            if (nextAssistant && nextAssistant.role === 'assistant' && prevAssistant && prevAssistant.role === 'assistant') {
                if (getMessageRichnessScore(nextAssistant) > getMessageRichnessScore(prevAssistant)) {
                    pass3[prevIdx + 1] = nextAssistant;
                }
                j++;
                continue;
            }
        }
        if (cur.role === 'user') {
            seenUserPairs.set(cur.content, pass3.length);
        }
        pass3.push(cur);
    }
    return pass3;
}

// 浏览器控制台工具：手动修复所有对话
window.repairAllChats = function() {
    const history = JSON.parse(localStorage.getItem('nanobot_chats') || '[]');
    let totalBefore = 0, totalAfter = 0;
    for (const h of history) {
        const raw = localStorage.getItem(`chat_${h.id}`);
        if (!raw) continue;
        try {
            const messages = JSON.parse(raw);
            if (!Array.isArray(messages)) continue;
            totalBefore += messages.length;
            const cleaned = normalizeChatHistory(messages);
            totalAfter += cleaned.length;
            if (cleaned.length < messages.length) {
                localStorage.setItem(`chat_${h.id}`, JSON.stringify(cleaned));
                console.log(`✅ chat_${h.id} (${h.title}): ${messages.length} → ${cleaned.length} messages`);
            }
        } catch (e) { /* skip */ }
    }
    console.log(`\n🔧 Repair complete: ${totalBefore} → ${totalAfter} total messages`);
    if (totalAfter < totalBefore) {
        console.log('Reloading page to apply changes...');
        location.reload();
    } else {
        console.log('No duplicates found.');
    }
};

function enableBatchMode() {
    batchModeEnabled = true;
    selectedChatIds = new Set();
    updateBatchToolbar();
    loadChatHistory();
}

function cancelBatchMode() {
    batchModeEnabled = false;
    selectedChatIds.clear();
    updateBatchToolbar();
    loadChatHistory();
}

function toggleChatSelection(chatId) {
    if (selectedChatIds.has(chatId)) {
        selectedChatIds.delete(chatId);
    } else {
        selectedChatIds.add(chatId);
    }
    updateBatchToolbar();
    if (batchModeEnabled) {
        const checkbox = document.querySelector(`.chat-item-checkbox input[data-chat-id="${chatId}"]`);
        if (checkbox) {
            checkbox.checked = selectedChatIds.has(chatId);
        }
    }
}

async function finalizeStoppedResponse(chatId, message, stoppedContent, attachments) {
    const savedMessages = localStorage.getItem(`chat_${chatId}`);
    let chatHistory = savedMessages ? JSON.parse(savedMessages) : [];

    const userMsgExists = chatHistory.some(m => m.role === 'user' && m.content === message);
    if (!userMsgExists) {
        chatHistory.push({ role: 'user', content: message, attachments });
    } else if (attachments && attachments.length > 0) {
        const existing = chatHistory.find(m => m.role === 'user' && m.content === message);
        if (existing && !existing.attachments?.length) existing.attachments = attachments;
    }

    const lastHistoryMsg = chatHistory[chatHistory.length - 1];
    if (lastHistoryMsg && lastHistoryMsg.role === 'assistant') {
        lastHistoryMsg.content = stoppedContent;
        lastHistoryMsg.streaming = false;
    } else {
        chatHistory.push({ role: 'assistant', content: stoppedContent, streaming: false });
    }

    chatHistory = normalizeChatHistory(chatHistory);
    localStorage.setItem(`chat_${chatId}`, JSON.stringify(chatHistory));

    const history = JSON.parse(localStorage.getItem('nanobot_chats') || '[]');
    const chatIndex = history.findIndex(h => h.id === chatId);
    if (chatIndex >= 0) {
        history[chatIndex].preview = stoppedContent.substring(0, 50) + '...';
        localStorage.setItem('nanobot_chats', JSON.stringify(history));
    }

    if (currentChatId === chatId) {
        messageHistory = normalizeChatHistory(chatHistory);
        loadChat(chatId);
    }
}

// ============== 远程模型发现功能 ==============

let discoveredModels = null;
let modelDiscoveryInProgress = false;

/**
 * 发现远程模型并更新 UI
 */
async function discoverRemoteModels() {
    if (modelDiscoveryInProgress) return;
    modelDiscoveryInProgress = true;
    
    console.log('[ModelDiscovery] 开始发现远程模型...');
    
    try {
        const response = await fetch('/api/models/discover');
        const data = await response.json();
        discoveredModels = data;
        
        console.log('[ModelDiscovery] 发现结果:', data);
        
        // 更新 UI
        updateModelSelectUI(data);
        updateBackendSelectUI(data);
        
        // 应用推荐默认值（强制应用，优先选择运行中的服务）
        if (data.recommendations) {
            const { default_backend, default_model } = data.recommendations;
            
            // 强制应用推荐的调用方式
            if (default_backend) {
                selectBackend(default_backend);
                console.log('[ModelDiscovery] 自动选择调用方式:', default_backend);
            }
            
            // 强制应用推荐的模型
            if (default_model) {
                selectModel(default_model);
                console.log('[ModelDiscovery] 自动选择模型:', default_model);
            }
        }
        
        return data;
    } catch (error) {
        console.error('[ModelDiscovery] 发现失败:', error);
        return null;
    } finally {
        modelDiscoveryInProgress = false;
    }
}

/**
 * 更新模型选择 UI
 */
function updateModelSelectUI(discoveryData) {
    const modelSelectMenu = document.getElementById('modelSelectMenu');
    if (!modelSelectMenu) return;
    
    // 清空现有内容
    modelSelectMenu.innerHTML = '';
    
    const ollamaInfo = discoveryData.ollama || {};
    const llamacppInfo = discoveryData.llamacpp || {};
    
    // 使用 all_models 字段（包含自动发现 + 静态配置）
    const ollamaModels = ollamaInfo.all_models || ollamaInfo.models || [];
    const llamacppModels = llamacppInfo.all_models || llamacppInfo.models || [];
    
    // 添加 Ollama 模型
    if (ollamaModels.length > 0) {
        const ollamaHeader = document.createElement('div');
        ollamaHeader.className = 'menu-section-header';
        const ollamaOnline = ollamaInfo.available ? '<span style="color: var(--success-color);">●</span>' : '<span style="color: var(--error-color);">○</span>';
        ollamaHeader.innerHTML = `${ollamaOnline} <i class="fas fa-cloud"></i> Ollama 模型`;
        ollamaHeader.style.cssText = 'padding: 6px 12px; font-size: 11px; color: var(--text-muted); border-bottom: 1px solid var(--border-color);';
        modelSelectMenu.appendChild(ollamaHeader);
        
        ollamaModels.forEach(model => {
            const item = createModelMenuItem(model, 'ollama');
            modelSelectMenu.appendChild(item);
        });
    }
    
    // 添加 llama.cpp 模型
    if (llamacppModels.length > 0) {
        const llamacppHeader = document.createElement('div');
        llamacppHeader.className = 'menu-section-header';
        const llamacppOnline = llamacppInfo.available ? '<span style="color: var(--success-color);">●</span>' : '<span style="color: var(--error-color);">○</span>';
        llamacppHeader.innerHTML = `${llamacppOnline} <i class="fas fa-microchip"></i> llama.cpp 模型`;
        llamacppHeader.style.cssText = 'padding: 6px 12px; font-size: 11px; color: var(--text-muted); border-bottom: 1px solid var(--border-color);';
        modelSelectMenu.appendChild(llamacppHeader);
        
        llamacppModels.forEach(model => {
            const item = createModelMenuItem(model, 'llamacpp');
            modelSelectMenu.appendChild(item);
        });
    }
    
    // 如果没有任何模型，显示提示
    if (ollamaModels.length === 0 && llamacppModels.length === 0) {
        const noModels = document.createElement('div');
        noModels.className = 'no-models-message';
        noModels.innerHTML = `
            <div style="padding: 12px; text-align: center; color: var(--text-muted);">
                <i class="fas fa-exclamation-triangle" style="font-size: 24px; margin-bottom: 8px;"></i>
                <div>未检测到可用模型</div>
                <div style="font-size: 11px; margin-top: 4px;">请检查 Win11 主机服务状态</div>
            </div>
        `;
        modelSelectMenu.appendChild(noModels);
    }
    
    // 添加刷新按钮
    const refreshBtn = document.createElement('div');
    refreshBtn.className = 'chat-item-menu-item';
    refreshBtn.style.cssText = 'border-top: 1px solid var(--border-color); color: var(--primary-color);';
    refreshBtn.innerHTML = '<i class="fas fa-sync-alt"></i><span>刷新模型列表</span>';
    refreshBtn.onclick = () => {
        discoveredModels = null;
        discoverRemoteModels();
    };
    modelSelectMenu.appendChild(refreshBtn);
}

/**
 * 创建模型菜单项
 */
function createModelMenuItem(model, backend) {
    const item = document.createElement('div');
    item.className = 'chat-item-menu-item model-item';
    
    const modelName = model.name || model.id || 'unknown';
    const isRunning = model.running === true;
    const isDiscovered = model.discovered === true;
    const size = model.size_display || formatModelSize(model.size) || model.owned_by || '';
    const modelType = model.type || 'chat';
    
    // 设置 data 属性
    item.dataset.model = modelName;
    item.dataset.backend = backend;
    
    // 根据模型类型选择图标
    let icon = 'fa-microchip';
    if (modelType === 'code' || modelName.includes('coder') || modelName.includes('code')) {
        icon = 'fa-code';
    } else if (modelType === 'reasoning' || modelName.includes('r1') || modelName.includes('deepseek-r1')) {
        icon = 'fa-brain';
    } else if (modelType === 'vision' || modelName.includes('vl') || modelName.includes('vision')) {
        icon = 'fa-eye';
    } else if (modelName.includes('122b') || modelName.includes('70b')) {
        icon = 'fa-rocket';
    } else if (modelType === 'uncensored') {
        icon = 'fa-fire';
    } else if (backend === 'ollama') {
        icon = 'fa-robot';
    }
    
    // 构建内容
    const statusIndicator = isRunning 
        ? '<span class="running-indicator" style="color: var(--success-color); margin-left: 4px;"><i class="fas fa-circle" style="font-size: 6px;"></i></span>'
        : '';
    
    item.innerHTML = `
        <i class="fas ${icon}"></i>
        <span>${modelName}</span>
        <small class="model-size">${size}</small>
        ${statusIndicator}
    `;
    
    // 样式：正在运行 vs 未运行（灰色）
    if (isRunning) {
        item.style.background = 'rgba(34, 197, 94, 0.1)';
        item.style.opacity = '1';
    } else if (!isDiscovered) {
        // 未发现的模型（静态配置）用灰色显示
        item.style.opacity = '0.5';
        item.style.color = 'var(--text-muted)';
    }
    
    // 添加提示
    if (isRunning) {
        item.title = `${modelName} - 正在运行`;
    } else if (isDiscovered) {
        item.title = `${modelName} - 已发现，未运行`;
    } else {
        item.title = `${modelName} - 静态配置，未检测到`;
    }
    
    return item;
}

/**
 * 更新调用方式选择 UI
 */
function updateBackendSelectUI(discoveryData) {
    const backendSelectMenu = document.getElementById('backendSelectMenu');
    if (!backendSelectMenu) return;
    
    const ollamaInfo = discoveryData.ollama || {};
    const llamacppInfo = discoveryData.llamacpp || {};
    const recommendations = discoveryData.recommendations || {};
    
    // 获取运行状态
    const ollamaRunning = ollamaInfo.available && (ollamaInfo.running_models || []).length > 0;
    const llamacppRunning = llamacppInfo.available && (llamacppInfo.models || []).length > 0;
    
    // 更新 Ollama 选项状态
    const ollamaItem = backendSelectMenu.querySelector('[data-backend="ollama"]');
    if (ollamaItem) {
        const statusIcon = ollamaItem.querySelector('.status-icon') || document.createElement('span');
        statusIcon.className = 'status-icon';
        statusIcon.style.cssText = 'margin-left: auto; font-size: 10px;';
        
        if (ollamaInfo.available) {
            statusIcon.innerHTML = '<i class="fas fa-check-circle" style="color: var(--success-color);"></i>';
            ollamaItem.style.opacity = '1';
        } else {
            statusIcon.innerHTML = '<i class="fas fa-times-circle" style="color: var(--error-color);"></i>';
            ollamaItem.style.opacity = '0.5';
        }
        
        if (!ollamaItem.contains(statusIcon)) {
            ollamaItem.appendChild(statusIcon);
        }
        
        // 显示运行中的模型数量
        const allOllamaModels = ollamaInfo.all_models || ollamaInfo.models || [];
        const runningCount = allOllamaModels.filter(m => m.running).length;
        const countBadge = ollamaItem.querySelector('.running-count') || document.createElement('small');
        countBadge.className = 'running-count model-size';
        if (runningCount > 0) {
            countBadge.textContent = `${runningCount} 运行中`;
            countBadge.style.color = 'var(--success-color)';
        } else if (ollamaInfo.available) {
            countBadge.textContent = `${allOllamaModels.length} 可用`;
        } else {
            countBadge.textContent = '离线';
        }
        if (!ollamaItem.contains(countBadge)) {
            ollamaItem.appendChild(countBadge);
        }
        
        // 标注推荐选择
        if (recommendations.default_backend === 'ollama') {
            ollamaItem.classList.add('recommended');
            const recommendedBadge = ollamaItem.querySelector('.recommended-badge') || document.createElement('span');
            recommendedBadge.className = 'recommended-badge';
            recommendedBadge.style.cssText = 'background: var(--success-color); color: white; font-size: 9px; padding: 1px 4px; border-radius: 3px; margin-left: 4px;';
            recommendedBadge.textContent = '已选择';
            if (!ollamaItem.contains(recommendedBadge)) {
                ollamaItem.appendChild(recommendedBadge);
            }
        } else {
            ollamaItem.classList.remove('recommended');
            const badge = ollamaItem.querySelector('.recommended-badge');
            if (badge) badge.remove();
        }
    }
    
    // 更新 llama.cpp 选项状态
    const llamacppItem = backendSelectMenu.querySelector('[data-backend="ollm"]');
    if (llamacppItem) {
        const statusIcon = llamacppItem.querySelector('.status-icon') || document.createElement('span');
        statusIcon.className = 'status-icon';
        statusIcon.style.cssText = 'margin-left: auto; font-size: 10px;';
        
        if (llamacppInfo.available) {
            statusIcon.innerHTML = '<i class="fas fa-check-circle" style="color: var(--success-color);"></i>';
            llamacppItem.style.opacity = '1';
        } else {
            statusIcon.innerHTML = '<i class="fas fa-times-circle" style="color: var(--error-color);"></i>';
            llamacppItem.style.opacity = '0.5';
        }
        
        if (!llamacppItem.contains(statusIcon)) {
            llamacppItem.appendChild(statusIcon);
        }
        
        // 显示模型数量
        const allLlamacppModels = llamacppInfo.all_models || llamacppInfo.models || [];
        const runningCount = allLlamacppModels.filter(m => m.running).length;
        const countBadge = llamacppItem.querySelector('.running-count') || document.createElement('small');
        countBadge.className = 'running-count model-size';
        if (runningCount > 0) {
            countBadge.textContent = `${runningCount} 运行中`;
            countBadge.style.color = 'var(--success-color)';
        } else if (llamacppInfo.available) {
            countBadge.textContent = `${allLlamacppModels.length} 可用`;
        } else {
            countBadge.textContent = '离线';
        }
        if (!llamacppItem.contains(countBadge)) {
            llamacppItem.appendChild(countBadge);
        }
        
        // 标注推荐选择
        if (recommendations.default_backend === 'ollm') {
            llamacppItem.classList.add('recommended');
            const recommendedBadge = llamacppItem.querySelector('.recommended-badge') || document.createElement('span');
            recommendedBadge.className = 'recommended-badge';
            recommendedBadge.style.cssText = 'background: var(--success-color); color: white; font-size: 9px; padding: 1px 4px; border-radius: 3px; margin-left: 4px;';
            recommendedBadge.textContent = '已选择';
            if (!llamacppItem.contains(recommendedBadge)) {
                llamacppItem.appendChild(recommendedBadge);
            }
        } else {
            llamacppItem.classList.remove('recommended');
            const badge = llamacppItem.querySelector('.recommended-badge');
            if (badge) badge.remove();
        }
    }
    
    // 更新调用方式按钮显示
    const backendDisplay = document.getElementById('currentBackendDisplay');
    if (backendDisplay && recommendations.default_backend) {
        const backendNames = {
            'ollama': 'Ollama',
            'ollm': 'llama.cpp'
        };
        const backendName = backendNames[recommendations.default_backend] || recommendations.default_backend;
        backendDisplay.textContent = backendName;
        backendDisplay.style.color = 'var(--success-color)';
    }
}

/**
 * 格式化模型大小
 */
function formatModelSize(bytes) {
    if (!bytes) return '';
    const gb = bytes / (1024 * 1024 * 1024);
    if (gb >= 1) {
        return gb.toFixed(1) + 'GB';
    }
    const mb = bytes / (1024 * 1024);
    return mb.toFixed(0) + 'MB';
}

/**
 * 选择模型（编程方式）
 */
function selectModel(modelName) {
    selectedModel = modelName;
    localStorage.setItem('nanobot_model', modelName);
    
    const modelDisplay = document.getElementById('currentModelDisplay');
    if (modelDisplay) {
        const shortName = modelName.split(':')[0].substring(0, 15);
        modelDisplay.textContent = shortName;
    }
    
    console.log('[ModelSelect] 选择模型:', modelName);
}

/**
 * 选择调用方式（编程方式）
 */
function selectBackend(backend) {
    selectedBackend = backend;
    localStorage.setItem('nanobot_backend', backend);
    
    const backendDisplay = document.getElementById('currentBackendDisplay');
    if (backendDisplay) {
        const names = { 'ollama': 'Ollama', 'ollm': 'llama.cpp' };
        backendDisplay.textContent = names[backend] || backend;
    }
    
    // 更新选中状态
    const backendSelectMenu = document.getElementById('backendSelectMenu');
    if (backendSelectMenu) {
        backendSelectMenu.querySelectorAll('.chat-item-menu-item').forEach(item => {
            item.classList.remove('selected');
            if (item.dataset.backend === backend) {
                item.classList.add('selected');
            }
        });
    }
    
    console.log('[BackendSelect] 选择调用方式:', backend);
}

// 页面加载时自动发现模型
document.addEventListener('DOMContentLoaded', () => {
    // 延迟执行，确保其他初始化完成
    setTimeout(() => {
        discoverRemoteModels();
    }, 1000);
});

function initInputToolbar() {
    const deepThink = document.getElementById('deepThinkToggle');
    const autoSearch = document.getElementById('autoSearchToggle');
    const toolsToggle = document.getElementById('toolsToggle');
    const toolsMenu = document.getElementById('toolsMenu');
    const modelSelectToggle = document.getElementById('modelSelectToggle');
    const modelSelectMenu = document.getElementById('modelSelectMenu');
    const backendSelectToggle = document.getElementById('backendSelectToggle');
    const backendSelectMenu = document.getElementById('backendSelectMenu');
    const uploadButton = document.getElementById('uploadButton');
    const uploadMenu = document.getElementById('uploadMenu');
    const uploadInput = document.getElementById('uploadFileInput');

    // Mode switcher (Code/Ask/Plan)
    const modeSwitcher = document.getElementById('modeSwitcher');
    if (modeSwitcher) {
        const modeSwitcherToggle = document.getElementById('modeSwitcherToggle');
        const modeSwitcherMenu = document.getElementById('modeSwitcherMenu');
        const modeSwitcherLabel = document.getElementById('modeSwitcherLabel');
        const modeSwitcherIcon = document.getElementById('modeSwitcherIcon');
        const modeHint = document.getElementById('modeHint');
        const modeBtns = modeSwitcher.querySelectorAll('.mode-btn');
        const modeMeta = {
            code: {
                label: 'Code',
                icon: 'fa-code',
                hint: '<strong>Code</strong>可读写代码和执行命令'
            },
            ask: {
                label: 'Ask',
                icon: 'fa-eye',
                hint: '<strong>Ask</strong>只读，不会修改文件'
            },
            plan: {
                label: 'Plan',
                icon: 'fa-list-check',
                hint: '<strong>Plan</strong>先计划，后执行'
            },
        };

        const setMode = (mode, { closeMenu = false } = {}) => {
            const normalizedMode = modeMeta[mode] ? mode : 'code';
            window._nanobotMode = normalizedMode;
            modeSwitcher.dataset.mode = normalizedMode;
            modeBtns.forEach(btn => {
                btn.classList.toggle('active', btn.dataset.mode === normalizedMode);
            });
            if (modeSwitcherLabel) {
                modeSwitcherLabel.textContent = modeMeta[normalizedMode].label;
            }
            if (modeSwitcherIcon) {
                modeSwitcherIcon.className = `fas ${modeMeta[normalizedMode].icon}`;
            }
            if (modeSwitcherToggle) {
                modeSwitcherToggle.title = `当前模式：${modeMeta[normalizedMode].label} · 点击切换`;
            }
            const input = document.getElementById('messageInput');
            if (input) {
                input.placeholder = '';
            }
            if (modeHint) {
                modeHint.innerHTML = modeMeta[normalizedMode].hint;
            }
            if (closeMenu) {
                modeSwitcher.classList.remove('open');
            }
        };

        if (modeSwitcherToggle && modeSwitcherMenu) {
            modeSwitcherToggle.addEventListener('click', (event) => {
                event.stopPropagation();
                modeSwitcher.classList.toggle('open');
            });

            modeSwitcherMenu.addEventListener('click', (event) => {
                const btn = event.target.closest('.mode-btn');
                if (!btn) return;
                event.stopPropagation();
                setMode(btn.dataset.mode, { closeMenu: true });
            });

            modeSwitcher.addEventListener('click', (event) => {
                event.stopPropagation();
            });

            document.addEventListener('click', () => {
                modeSwitcher.classList.remove('open');
            });
        }

        setMode('code');
    }

    if (deepThink) {
        deepThink.addEventListener('click', () => {
            deepThink.classList.toggle('active');
        });
    }
    if (autoSearch) {
        autoSearch.addEventListener('click', () => {
            autoSearch.classList.toggle('active');
        });
    }
    if (toolsToggle) {
        toolsToggle.addEventListener('click', () => {
            toolsToggle.classList.toggle('active');
            if (toolsMenu) {
                toolsMenu.classList.toggle('active');
            }
        });
    }
    if (toolsMenu) {
        toolsMenu.addEventListener('click', (event) => {
            const item = event.target.closest('.tools-menu-item');
            if (!item) return;
            const name = item.querySelector('span')?.textContent || '工具';
            if (toolsToggle) {
                toolsToggle.classList.add('active');
                toolsToggle.querySelector('.tool-name')?.remove();
                const label = document.createElement('span');
                label.className = 'tool-name';
                label.textContent = name;
                toolsToggle.appendChild(label);
            }
            toolsMenu.classList.remove('active');
        });
    }
    // 模型选择按钮逻辑
    let modelKeyboardIndex = -1; // 键盘导航索引

    if (modelSelectToggle) {
        modelSelectToggle.addEventListener('click', (e) => {
            modelSelectToggle.classList.toggle('active');
            if (modelSelectMenu) {
                const isOpen = modelSelectMenu.classList.contains('open');
                if (isOpen) {
                    // 关闭菜单 - 清除所有样式
                    modelSelectMenu.classList.remove('open');
                    modelSelectMenu.style.display = '';
                    modelSelectMenu.style.visibility = '';
                } else {
                    // 先显示菜单以获取正确高度
                    modelSelectMenu.classList.add('open');
                    modelSelectMenu.style.visibility = 'hidden';
                    modelSelectMenu.style.display = 'block';
                    
                    // 计算菜单位置 - 向上弹出
                    const rect = modelSelectToggle.getBoundingClientRect();
                    const menuHeight = modelSelectMenu.offsetHeight;
                    const menuWidth = modelSelectMenu.offsetWidth;
                    
                    // 确保菜单不超出屏幕顶部
                    let topPos = rect.top - menuHeight - 8;
                    if (topPos < 10) {
                        topPos = 10; // 最小距离顶部10px
                    }
                    
                    // 确保菜单不超出屏幕右侧
                    let leftPos = rect.left;
                    if (leftPos + menuWidth > window.innerWidth - 10) {
                        leftPos = window.innerWidth - menuWidth - 10;
                    }
                    
                    modelSelectMenu.style.left = leftPos + 'px';
                    modelSelectMenu.style.top = topPos + 'px';
                    modelSelectMenu.style.visibility = 'visible';
                    
                    // 重置键盘索引到当前选中项
                    modelKeyboardIndex = -1;
                    const items = modelSelectMenu.querySelectorAll('.chat-item-menu-item');
                    items.forEach((item, idx) => {
                        if (item.classList.contains('selected')) {
                            modelKeyboardIndex = idx;
                        }
                    });
                    // 聚焦到菜单以接收键盘事件
                    modelSelectMenu.focus();
                }
            }
        });
    }

    // 键盘导航处理
    function handleModelKeyboard(e) {
        if (!modelSelectMenu || !modelSelectMenu.classList.contains('open')) return;

        const items = modelSelectMenu.querySelectorAll('.chat-item-menu-item');
        if (items.length === 0) return;

        if (e.key === 'ArrowDown') {
            e.preventDefault();
            modelKeyboardIndex = (modelKeyboardIndex + 1) % items.length;
            updateKeyboardFocus(items);
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            modelKeyboardIndex = modelKeyboardIndex <= 0 ? items.length - 1 : modelKeyboardIndex - 1;
            updateKeyboardFocus(items);
        } else if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            if (modelKeyboardIndex >= 0 && modelKeyboardIndex < items.length) {
                selectModelItem(items[modelKeyboardIndex]);
            }
        } else if (e.key === 'Escape') {
            e.preventDefault();
            modelSelectMenu.classList.remove('open');
            modelSelectMenu.style.display = '';
            modelSelectMenu.style.visibility = '';
            clearKeyboardFocus(items);
        }
    }

    function updateKeyboardFocus(items) {
        clearKeyboardFocus(items);
        if (modelKeyboardIndex >= 0 && modelKeyboardIndex < items.length) {
            items[modelKeyboardIndex].classList.add('keyboard-focus');
            items[modelKeyboardIndex].scrollIntoView({ block: 'nearest' });
        }
    }

    function clearKeyboardFocus(items) {
        items.forEach(item => item.classList.remove('keyboard-focus'));
    }

    function selectModelItem(item) {
        const modelId = item.dataset.model;
        const modelName = item.querySelector('span')?.textContent || modelId;
        if (modelId) {
            selectedModel = modelId;
            // 更新按钮显示
            if (modelSelectToggle) {
                modelSelectToggle.classList.add('active');
            }
            // 更新当前模型显示
            const modelDisplay = document.getElementById('currentModelDisplay');
            if (modelDisplay) {
                // 提取简短名称（取括号前的部分或前15个字符）
                const shortName = modelName.split('(')[0].trim().substring(0, 20);
                modelDisplay.textContent = shortName;
            }
            // 更新选中状态
            modelSelectMenu.querySelectorAll('.chat-item-menu-item').forEach(i => {
                i.classList.remove('selected');
            });
            item.classList.add('selected');
            console.log('[ModelSelect] 切换模型:', selectedModel);
        }
        // 关闭菜单 - 清除所有样式
        modelSelectMenu.classList.remove('open');
        modelSelectMenu.style.display = '';
        modelSelectMenu.style.visibility = '';
        clearKeyboardFocus(modelSelectMenu.querySelectorAll('.chat-item-menu-item'));
    }

    if (modelSelectMenu) {
        // 使菜单可聚焦以接收键盘事件
        modelSelectMenu.setAttribute('tabindex', '-1');
        modelSelectMenu.addEventListener('keydown', handleModelKeyboard);

        modelSelectMenu.addEventListener('click', (event) => {
            const item = event.target.closest('.chat-item-menu-item');
            if (!item) return;
            selectModelItem(item);
        });

        // 初始化选中状态
        const defaultItem = modelSelectMenu.querySelector(`[data-model="${selectedModel}"]`);
        if (defaultItem) {
            defaultItem.classList.add('selected');
            // 初始化当前模型显示
            const modelName = defaultItem.querySelector('span')?.textContent || selectedModel;
            const modelDisplay = document.getElementById('currentModelDisplay');
            if (modelDisplay) {
                const shortName = modelName.split('(')[0].trim().substring(0, 20);
                modelDisplay.textContent = shortName;
            }
        }
    }

    // 调用方式选择按钮逻辑
    if (backendSelectToggle) {
        backendSelectToggle.addEventListener('click', (e) => {
            backendSelectToggle.classList.toggle('active');
            if (backendSelectMenu) {
                const isOpen = backendSelectMenu.classList.contains('open');
                if (isOpen) {
                    backendSelectMenu.classList.remove('open');
                    backendSelectMenu.style.display = '';
                    backendSelectMenu.style.visibility = '';
                } else {
                    backendSelectMenu.classList.add('open');
                    backendSelectMenu.style.visibility = 'hidden';
                    backendSelectMenu.style.display = 'block';
                    
                    const rect = backendSelectToggle.getBoundingClientRect();
                    const menuHeight = backendSelectMenu.offsetHeight;
                    const menuWidth = backendSelectMenu.offsetWidth;
                    
                    let topPos = rect.top - menuHeight - 8;
                    if (topPos < 10) topPos = 10;
                    
                    let leftPos = rect.left;
                    if (leftPos + menuWidth > window.innerWidth - 10) {
                        leftPos = window.innerWidth - menuWidth - 10;
                    }
                    
                    backendSelectMenu.style.left = leftPos + 'px';
                    backendSelectMenu.style.top = topPos + 'px';
                    backendSelectMenu.style.visibility = 'visible';
                }
            }
        });
    }

    // 调用方式菜单项点击
    function selectBackendItem(item) {
        const backendId = item.dataset.backend;
        const backendName = item.querySelector('span')?.textContent || backendId;
        if (backendId) {
            selectedBackend = backendId;
            // 更新按钮显示
            if (backendSelectToggle) {
                const backendDisplay = document.getElementById('currentBackendDisplay');
                if (backendDisplay) {
                    backendDisplay.textContent = backendId === 'ollama' ? 'Ollama' : 'llama.cpp';
                }
            }
            // 更新选中状态
            backendSelectMenu.querySelectorAll('.chat-item-menu-item').forEach(i => {
                i.classList.remove('selected');
            });
            item.classList.add('selected');
            console.log('[BackendSelect] 切换调用方式:', selectedBackend);
        }
        backendSelectMenu.classList.remove('open');
        backendSelectMenu.style.display = '';
        backendSelectMenu.style.visibility = '';
    }

    if (backendSelectMenu) {
        backendSelectMenu.addEventListener('click', (event) => {
            const item = event.target.closest('.chat-item-menu-item');
            if (!item) return;
            selectBackendItem(item);
        });

        // 初始化选中状态
        const defaultBackendItem = backendSelectMenu.querySelector(`[data-backend="${selectedBackend}"]`);
        if (defaultBackendItem) {
            defaultBackendItem.classList.add('selected');
        }
    }
    if (uploadButton) {
        uploadButton.addEventListener('click', () => {
            if (uploadMenu) {
                uploadMenu.classList.toggle('active');
            }
        });
    }
    if (uploadMenu) {
        uploadMenu.addEventListener('click', (event) => {
            const item = event.target.closest('.upload-menu-item');
            if (!item || !uploadInput) return;
            const uploadType = item.dataset.upload;
            uploadInput.value = '';
            uploadInput.accept = uploadType === 'image' ? 'image/*' : '*/*';
            uploadInput.click();
            uploadMenu.classList.remove('active');
        });
    }
    if (uploadInput) {
        uploadInput.addEventListener('change', (event) => {
            const file = event.target.files?.[0];
            if (!file) return;
            addAttachment(file);
        });
    }
    document.addEventListener('click', (event) => {
        if (toolsMenu && toolsToggle) {
            if (!toolsMenu.contains(event.target) && !toolsToggle.contains(event.target)) {
                toolsMenu.classList.remove('active');
            }
        }
        if (modelSelectMenu && modelSelectToggle) {
            if (!modelSelectMenu.contains(event.target) && !modelSelectToggle.contains(event.target)) {
                // 关闭菜单 - 清除所有样式
                modelSelectMenu.classList.remove('open');
                modelSelectMenu.style.display = '';
                modelSelectMenu.style.visibility = '';
            }
        }
        if (backendSelectMenu && backendSelectToggle) {
            if (!backendSelectMenu.contains(event.target) && !backendSelectToggle.contains(event.target)) {
                backendSelectMenu.classList.remove('open');
                backendSelectMenu.style.display = '';
                backendSelectMenu.style.visibility = '';
            }
        }
        if (uploadMenu && uploadButton) {
            if (!uploadMenu.contains(event.target) && !uploadButton.contains(event.target)) {
                uploadMenu.classList.remove('active');
            }
        }
    });
}

function toggleHomePage(show) {
    const home = document.getElementById('homePage');
    const favorites = document.getElementById('favoritesPanel');
    const chatHeader = document.querySelector('.chat-header');
    const messages = document.getElementById('messages');
    const inputArea = document.querySelector('.input-container');
    if (!home) return;
    if (show) {
        home.classList.add('active');
        home.style.display = '';  // 让CSS的.active控制显示
        home.style.setProperty('display', 'flex', 'important');
        if (favorites) favorites.classList.remove('active');
        if (chatHeader) chatHeader.style.display = 'none';
        if (messages) messages.style.display = 'none';
        if (inputArea) inputArea.style.display = 'none';
    } else {
        home.classList.remove('active');
        home.style.setProperty('display', 'none', 'important');  // 强制隐藏，使用!important
        if (favorites) favorites.classList.remove('active');
        if (chatHeader) chatHeader.style.display = '';
        if (messages) messages.style.display = '';
        if (inputArea) inputArea.style.display = '';
    }
}

function updateBatchToolbar() {
    const toolbar = document.getElementById('historyBatchToolbar');
    const countEl = document.getElementById('batchSelectedCount');
    if (!toolbar || !countEl) return;
    if (batchModeEnabled) {
        toolbar.classList.add('visible');
    } else {
        toolbar.classList.remove('visible');
    }
    countEl.textContent = selectedChatIds.size.toString();
}

function batchDelete() {
    if (selectedChatIds.size === 0) {
        alert('请先选择要删除的会话');
        return;
    }
    deleteChats(Array.from(selectedChatIds));
    cancelBatchMode();
}

function batchPin() {
    if (selectedChatIds.size === 0) {
        alert('请先选择要置顶的会话');
        return;
    }
    setPinnedForChats(Array.from(selectedChatIds), true);
    loadChatHistory();
}

function batchGroup() {
    if (selectedChatIds.size === 0) {
        alert('请先选择要分组的会话');
        return;
    }
    const groupName = prompt('请输入分组名称', '未分组');
    if (groupName) {
        moveChatsToGroup(Array.from(selectedChatIds), groupName.trim());
    }
}

function togglePinForChat(chatId) {
    const history = JSON.parse(localStorage.getItem('nanobot_chats') || '[]');
    const chat = history.find(item => item.id === chatId);
    if (!chat) return;
    chat.pinned = !chat.pinned;
    chat.pinnedAt = chat.pinned ? Date.now() : 0;
    localStorage.setItem('nanobot_chats', JSON.stringify(history));
    loadChatHistory();
}

function setPinnedForChats(chatIds, pinned) {
    const history = JSON.parse(localStorage.getItem('nanobot_chats') || '[]');
    const now = Date.now();
    history.forEach(item => {
        if (chatIds.includes(item.id)) {
            item.pinned = pinned;
            item.pinnedAt = pinned ? now : 0;
        }
    });
    localStorage.setItem('nanobot_chats', JSON.stringify(history));
    loadChatHistory();
}

function moveChatsToGroup(chatIds, groupName) {
    const history = JSON.parse(localStorage.getItem('nanobot_chats') || '[]');
    history.forEach(item => {
        if (chatIds.includes(item.id)) {
            item.group = groupName || '未分组';
        }
    });
    localStorage.setItem('nanobot_chats', JSON.stringify(history));
    loadChatHistory();
}

function renameChat(chatId, newTitle) {
    const history = JSON.parse(localStorage.getItem('nanobot_chats') || '[]');
    const chat = history.find(item => item.id === chatId);
    if (!chat) return;
    chat.title = newTitle;
    localStorage.setItem('nanobot_chats', JSON.stringify(history));
    if (chatId === currentChatId) {
        document.getElementById('chatTitle').textContent = newTitle;
    }
    loadChatHistory();
}

function deleteChats(chatIds) {
    if (!Array.isArray(chatIds) || chatIds.length === 0) return;
    const confirmText = chatIds.length > 1 ? `确定要删除选中的 ${chatIds.length} 条会话吗？` : '确定要删除当前会话吗？';
    if (!confirm(confirmText)) return;
    let history = JSON.parse(localStorage.getItem('nanobot_chats') || '[]');
    history = history.filter(item => !chatIds.includes(item.id));
    localStorage.setItem('nanobot_chats', JSON.stringify(history));
    chatIds.forEach(id => {
        localStorage.removeItem(`chat_${id}`);
        localStorage.removeItem(`streaming_${id}`);
    });
    if (chatIds.includes(currentChatId)) {
        if (history.length > 0) {
            const nextChat = getOrderedHistory(history)[0];
            if (nextChat) {
                loadChat(nextChat.id);
            }
        } else {
            startNewChat();
        }
    } else {
        loadChatHistory();
    }
}

function toggleHistoryMenu(event) {
    event.stopPropagation();
    const menu = document.getElementById('historyMenu');
    const button = document.getElementById('historyMenuButton');
    if (!menu || !button) {
        console.error('[toggleHistoryMenu] Menu or button not found');
        return;
    }
    
    // 先切换显示状态
    const isOpen = menu.classList.contains('open');
    if (isOpen) {
        menu.classList.remove('open');
        return;
    }
    
    // 关闭其他菜单
    closeChatItemMenu();
    
    // 将菜单移动到body级别，避免被父元素的backdrop-filter裁剪
    if (menu.parentElement !== document.body) {
        document.body.appendChild(menu);
    }
    
    // 先显示菜单才能获取正确位置
    menu.classList.add('open');
    
    // 计算按钮位置 - 菜单左上角对齐到按钮
    const rect = button.getBoundingClientRect();
    const menuWidth = menu.offsetWidth || 120;
    const menuHeight = menu.offsetHeight || 160;
    let left = rect.left;  // 菜单左上角对齐按钮左上角
    let top = rect.bottom + 4;  // 按钮下方4px
    
    // 边界检查：确保不超出视口
    if (left < 8) left = 8;
    if (left + menuWidth > window.innerWidth - 8) {
        left = window.innerWidth - menuWidth - 8;
    }
    if (top + menuHeight > window.innerHeight - 8) {
        top = rect.top - menuHeight - 4; // 如果下方放不下，显示在按钮上方
    }
    if (top < 8) top = 8;
    
    console.log('[toggleHistoryMenu] Button rect:', rect);
    console.log('[toggleHistoryMenu] Menu size:', menuWidth, menuHeight);
    console.log('[toggleHistoryMenu] Setting position - left:', left, 'top:', top);
    
    menu.style.position = 'fixed';
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
}

function closeHistoryMenuOnOutsideClick(event) {
    const menu = document.getElementById('historyMenu');
    const button = document.getElementById('historyMenuButton');
    if (!menu || !button) return;
    if (menu.contains(event.target) || button.contains(event.target)) {
        return;
    }
    menu.classList.remove('open');
    closeChatItemMenu();
}

function initChatSearch() {
    const input = document.getElementById('chatSearchInput');
    const clearButton = document.getElementById('chatSearchClear');
    if (!input || !clearButton) return;
    input.addEventListener('input', handleChatSearch);
    clearButton.addEventListener('click', () => {
        input.value = '';
        handleChatSearch();
        input.focus();
    });
}

function handleChatSearch() {
    const input = document.getElementById('chatSearchInput');
    const sidebar = document.getElementById('sidebar');
    const resultList = document.getElementById('chatSearchResultList');
    if (!input || !sidebar || !resultList) return;
    const query = input.value.trim();
    if (!query) {
        sidebar.classList.remove('search-active');
        resultList.innerHTML = '';
        return;
    }
    const history = JSON.parse(localStorage.getItem('nanobot_chats') || '[]');
    const queryLower = query.toLowerCase();
    const results = history.filter(item => {
        const title = item.title || '';
        const preview = item.preview || '';
        return title.toLowerCase().includes(queryLower) || preview.toLowerCase().includes(queryLower);
    });
    resultList.innerHTML = results.map(item => {
        const title = highlightSearch(item.title || '未命名会话', query);
        const preview = highlightSearch(item.preview || '', query);
        return `
            <div class="chat-search-result-item" onclick="loadChat('${item.id}')">
                <div class="chat-search-result-title">${title}</div>
                <div class="chat-search-result-abstract">${preview}</div>
            </div>
        `;
    }).join('') || '<div class="chat-search-result-abstract">没有匹配结果</div>';
    sidebar.classList.add('search-active');
}

function highlightSearch(text, query) {
    if (!query) return text;
    const escaped = query.replace(/[.*+?^${}()|[\\]\\]/g, '\\$&');
    return text.replace(new RegExp(escaped, 'gi'), (match) => `<mark>${match}</mark>`);
}

function initFavoritesPanel() {
    const tabs = document.querySelectorAll('.favorites-tab-btn');
    tabs.forEach((tab) => {
        tab.addEventListener('click', () => {
            tabs.forEach(btn => btn.classList.remove('active'));
            tab.classList.add('active');
        });
    });
}

function toggleFavoritesPanel(show) {
    const panel = document.getElementById('favoritesPanel');
    const chatHeader = document.querySelector('.chat-header');
    const messages = document.getElementById('messages');
    const inputArea = document.querySelector('.input-container');
    if (!panel) return;
    if (show) {
        panel.classList.add('active');
        if (chatHeader) chatHeader.style.display = 'none';
        if (messages) messages.style.display = 'none';
        if (inputArea) inputArea.style.display = 'none';
    } else {
        panel.classList.remove('active');
        if (chatHeader) chatHeader.style.display = '';
        if (messages) messages.style.display = '';
        if (inputArea) inputArea.style.display = '';
    }
}

function handleHistoryMenuAction(action) {
    const menu = document.getElementById('historyMenu');
    if (menu) {
        menu.classList.remove('open');
    }
    const labels = {
        batch: '批量操作',
        group: '移动到分组',
        rename: '编辑名称',
        share: '分享',
        pin: '置顶',
        delete: '删除'
    };
    if (action === 'batch') {
        enableBatchMode();
        return;
    }
    if (action === 'pin') {
        togglePinForChat(currentChatId);
        return;
    }
    if (action === 'delete') {
        deleteChats([currentChatId]);
        return;
    }
    if (action === 'group') {
        const groupName = prompt('请输入分组名称', '未分组');
        if (groupName) {
            moveChatsToGroup([currentChatId], groupName.trim());
        }
        return;
    }
    if (action === 'rename') {
        const newName = prompt('请输入新的名称', document.getElementById('chatTitle')?.textContent || '');
        if (newName) {
            renameChat(currentChatId, newName.trim());
        }
        return;
    }
    alert(`${labels[action] || action} 功能暂未实现`);
}

function openChatItemMenu(chatId, event) {
    event.preventDefault();
    event.stopPropagation();
    if (batchModeEnabled) return;
    const menu = document.getElementById('chatItemMenu');
    if (!menu) return;
    const history = JSON.parse(localStorage.getItem('nanobot_chats') || '[]');
    const chat = history.find(item => item.id === chatId);
    if (!chat) return;
    const pinLabel = chat.pinned ? '取消置顶' : '置顶';
    menu.innerHTML = `
        <div class="chat-item-menu-item" onclick="handleChatItemAction('pin', '${chatId}')">${pinLabel}</div>
        <div class="chat-item-menu-item" onclick="handleChatItemAction('group', '${chatId}')">移动到分组</div>
        <div class="chat-item-menu-item" onclick="handleChatItemAction('rename', '${chatId}')">编辑名称</div>
        <div class="chat-item-menu-item" onclick="handleChatItemAction('delete', '${chatId}')">删除</div>
    `;
    
    // 关闭其他菜单
    const historyMenu = document.getElementById('historyMenu');
    if (historyMenu) historyMenu.classList.remove('open');
    
    // 将菜单移动到body级别，避免被父元素的backdrop-filter裁剪
    if (menu.parentElement !== document.body) {
        document.body.appendChild(menu);
    }
    
    // 先显示菜单才能获取正确尺寸
    menu.classList.add('open');
    
    // 获取触发按钮的位置，菜单显示在按钮右侧
    const button = event.target.closest('.chat-item-action') || event.target;
    const rect = button.getBoundingClientRect();
    const menuWidth = menu.offsetWidth || 100;
    const menuHeight = menu.offsetHeight || 120;
    
    // 计算位置：按钮右侧，垂直居中
    let left = rect.right + 4;
    let top = rect.top + (rect.height / 2) - (menuHeight / 2);
    
    // 边界检查 - 确保菜单在可视区域内
    if (left < 8) left = 8;
    if (left + menuWidth > window.innerWidth - 8) {
        left = rect.left - menuWidth - 4; // 如果右边放不下，显示在左边
    }
    if (top < 8) top = 8;
    if (top + menuHeight > window.innerHeight - 8) {
        top = window.innerHeight - menuHeight - 8;
    }
    
    menu.style.position = 'fixed';
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
}

function closeChatItemMenu() {
    const menu = document.getElementById('chatItemMenu');
    if (menu) {
        menu.classList.remove('open');
    }
}

function handleChatItemAction(action, chatId) {
    closeChatItemMenu();
    if (action === 'pin') {
        togglePinForChat(chatId);
        return;
    }
    if (action === 'delete') {
        deleteChats([chatId]);
        return;
    }
    if (action === 'group') {
        const groupName = prompt('请输入分组名称', '未分组');
        if (groupName) {
            moveChatsToGroup([chatId], groupName.trim());
        }
        return;
    }
    if (action === 'rename') {
        const history = JSON.parse(localStorage.getItem('nanobot_chats') || '[]');
        const chat = history.find(item => item.id === chatId);
        const newName = prompt('请输入新的名称', chat?.title || '');
        if (newName) {
            renameChat(chatId, newName.trim());
        }
        return;
    }
}

async function resumeBackendStream(chatId, messagesDiv) {
    if (!CONFIG.ENABLE_BACKEND_HISTORY) return;
    try {
        const response = await fetch(`${CONFIG.API_URL}/api/sessions/${chatId}/stream_state`);
        if (!response.ok) {
            return;
        }
        const data = await response.json();
        const streamState = data.stream_state;
        if (!streamState || !streamState.status || streamState.status === 'done') {
            return;
        }

        const existingStreaming = messagesDiv.querySelector('#streamingMessage');
        if (existingStreaming) {
            existingStreaming.remove();
        }

        const streamingDiv = document.createElement('div');
        streamingDiv.className = 'message bot streaming';
        streamingDiv.id = 'streamingMessage';

        renderStreamingFrame(streamingDiv, {
            icon: 'fa-robot',
            markdownContent: streamState.content || '🤔 AI正在思考中...'
        });
        messagesDiv.appendChild(streamingDiv);
        scrollToBottom();

        localStorage.setItem(`streaming_${chatId}`, JSON.stringify({
            isStreaming: true,
            content: streamState.content || '',
            timestamp: Date.now()
        }));
        startStreamingPoll(chatId);
    } catch (error) {
        console.log('[resumeBackendStream] Failed:', error.message);
    }
}

// 加载最近的聊天记录
async function loadMostRecentChat() {
    const history = JSON.parse(localStorage.getItem('nanobot_chats') || '[]');
    console.log('[loadMostRecentChat] === START === history length:', history.length);
    
    if (history.length === 0) {
        console.log('[loadMostRecentChat] No history, returning false');
        return false;
    }
    
    // 获取最近的一条聊天记录（按时间戳排序）
    const orderedHistory = getOrderedHistory(history);
    const mostRecent = orderedHistory[0];
    console.log('[loadMostRecentChat] Most recent chat:', mostRecent.id, 'title:', mostRecent.title);
    
    if (mostRecent && mostRecent.id) {
        // 加载这条聊天记录
        const savedMessages = localStorage.getItem(`chat_${mostRecent.id}`);
        console.log('[loadMostRecentChat] savedMessages type:', typeof savedMessages, 'value:', savedMessages?.substring(0, 100));
        
        // 确保有实际的消息内容，否则显示欢迎消息
        if (savedMessages && savedMessages !== '[]') {
            try {
                messageHistory = normalizeChatHistory(hydrateHistoryAttachments(JSON.parse(savedMessages)));
            } catch (e) {
                console.error('[loadMostRecentChat] Failed to parse savedMessages:', e);
                return false;
            }
            currentChatId = mostRecent.id;
            currentSession = currentChatId;
            console.log('[loadMostRecentChat] messageHistory length:', messageHistory.length);
            console.log('[loadMostRecentChat] First msg:', messageHistory[0]?.role, 'Last msg:', messageHistory[messageHistory.length-1]?.role);

            if (CONFIG.ENABLE_BACKEND_HISTORY) {
                try {
                    const backendHistory = await fetchSessionHistory(currentChatId);
                    if (Array.isArray(backendHistory) && backendHistory.length > 0) {
                        const mergedHistory = mergeHistoryPreferLocal(messageHistory, backendHistory);
                        const mergedSerialized = JSON.stringify(mergedHistory);
                        const currentSerialized = JSON.stringify(messageHistory);
                        if (mergedSerialized !== currentSerialized) {
                            console.log('[loadMostRecentChat] Merged richer backend history into startup restore');
                            messageHistory = mergedHistory;
                            localStorage.setItem(`chat_${currentChatId}`, mergedSerialized);
                        }
                    }
                } catch (error) {
                    console.log('[loadMostRecentChat] Backend history merge skipped:', error?.message || error);
                }
            }
            
            // 如果消息历史为空，也返回 false 显示欢迎消息
            if (!messageHistory || messageHistory.length === 0) {
                console.log('[loadMostRecentChat] Empty message history after parse, returning false');
                return false;
            }
            
            // 重建界面
            const messagesDiv = document.getElementById('messages');
            console.log('[loadMostRecentChat] Clearing messages div');
            messagesDiv.innerHTML = '';
            
            // 找到最后一条AI消息的索引
            let lastAssistantIndex = -1;
            for (let i = messageHistory.length - 1; i >= 0; i--) {
                if (messageHistory[i].role === 'assistant') {
                    lastAssistantIndex = i;
                    break;
                }
            }
            console.log('[loadMostRecentChat] lastAssistantIndex:', lastAssistantIndex);
            
            // 检查是否有未完成的流式响应需要恢复
            const streamingState = localStorage.getItem(`streaming_${currentChatId}`);
            const hasStreamingState = Boolean(streamingState);

            // 渲染消息，只有最后一条AI消息显示完成标识
            console.log('[loadMostRecentChat] Rendering', messageHistory.length, 'messages');
            messageHistory.forEach((msg, index) => {
                const isStreaming = Boolean(msg.streaming);
                if (isStreaming && hasStreamingState) {
                    return;
                }
                const isLatest = !isStreaming && (index === lastAssistantIndex);
            console.log(`[loadMostRecentChat] Rendering msg ${index}: role=${msg.role}, isLatest=${isLatest}, streaming=${isStreaming}`);
            addMessageToUI(msg.role, msg.content, isLatest, isStreaming, msg.attachments || [], {
                elapsedMs: msg.elapsed_ms,
                stats: msg.llama_stats,
                finalAnswer: msg.final_answer,
                thinkingTrace: msg.thinking_trace || '',
                agenticTranscriptMarkdown: msg.agentic_transcript || msg.full_response_markdown || msg.thinking_trace || '',
                changeSets: msg.change_sets || [],
                pendingChangeSetIds: msg.pending_change_set_ids || []
            });
        });
        schedulePendingApprovalRestore(currentSession || currentChatId);
        
    
            // 检查是否有未完成的流式响应需要恢复
            console.log('[loadMostRecentChat] streamingState:', streamingState);
            
            if (streamingState) {
                try {
                    const state = JSON.parse(streamingState);
                    console.log('[loadMostRecentChat] Parsed streaming state:', state);
                    
                    if (state.isStreaming) {
                        const ageMs = Date.now() - (state.timestamp || 0);
                        if (ageMs > CONFIG.STREAMING_STALE_MS) {
                            console.log('[loadMostRecentChat] Streaming state stale, clearing');
                            localStorage.removeItem(`streaming_${currentChatId}`);
                            return true;
                        }
                        console.log('[loadMostRecentChat] Restoring streaming message, content length:', state.content?.length || 0);
                        
                        // 确保流式消息元素不存在（避免重复）
                        const existingStreaming = messagesDiv.querySelector('#streamingMessage');
                        if (existingStreaming) {
                            existingStreaming.remove();
                        }
                        
                        // 恢复流式消息显示
                        const streamingDiv = document.createElement('div');
                        streamingDiv.className = 'message bot streaming';
                        streamingDiv.id = 'streamingMessage';

                        renderStreamingFrame(streamingDiv, {
                            icon: 'fa-robot',
                            markdownContent: state.content || '🤔 AI正在思考中...'
                        });
                        messagesDiv.appendChild(streamingDiv);
                        scrollToBottom();
                        console.log('[loadMostRecentChat] Streaming message restored');
                        
                        // 启动轮询检查流式状态更新
                        console.log('[loadMostRecentChat] Starting streaming poll for', currentChatId);
                        startStreamingPoll(currentChatId);
                    } else {
                        console.log('[loadMostRecentChat] isStreaming is false, removing streaming state');
                        localStorage.removeItem(`streaming_${currentChatId}`);
                    }
                } catch (e) {
                    console.error('[loadMostRecentChat] Error parsing streaming state:', e);
                    localStorage.removeItem(`streaming_${currentChatId}`);
                }
            } else {
                console.log('[loadMostRecentChat] No streaming state found');
            }
            
            // 更新标题
            document.getElementById('chatTitle').textContent = mostRecent.title || '历史对话';

            syncTaskStatusBarForActiveSession(currentSession || currentChatId, { force: true }).catch((error) => {
                console.warn('[loadMostRecentChat] Failed to refresh task status bar:', error);
            });
            syncFileChangeSummaryForActiveSession(currentSession || currentChatId, {
                expanded: fileChangeSummaryState.expanded,
            }).catch((error) => {
                console.warn('[loadMostRecentChat] Failed to refresh file change summary:', error);
            });
            
            console.log('[loadMostRecentChat] === END === returning true');
            return true;
        }
        console.log('[loadMostRecentChat] savedMessages is empty or null, returning false');
    }
    
    console.log('[loadMostRecentChat] === END === returning false');
    return false;
}

// 显示欢迎消息
function showWelcomeMessage() {
    console.log('[showWelcomeMessage] Displaying welcome message');
    const messagesDiv = document.getElementById('messages');
    console.log('[showWelcomeMessage] messagesDiv before:', messagesDiv.innerHTML.substring(0, 100));
    messagesDiv.innerHTML = `
        <div class="message bot">
            <div class="avatar">
                <i class="fas fa-robot"></i>
            </div>
            <div class="message-content">
                <p>👋 你好！我是 <strong>Nanobot</strong>，你的个人 AI 助手。</p>
                <p>我支持：</p>
                <ul style="margin-left: 20px; margin-top: 8px;">
                    <li>💻 代码编写与调试</li>
                    <li>🔍 文件操作与搜索</li>
                    <li>🌐 网络搜索与信息查询</li>
                    <li>🖼️ 图片分析（使用 <code>vision_helper.py</code>）</li>
                </ul>
                <p style="margin-top: 12px;">有什么我可以帮你的吗？</p>
            </div>
        </div>
    `;
    console.log('[showWelcomeMessage] messagesDiv after:', messagesDiv.innerHTML.substring(0, 100));
}

// 检查后端连接
async function checkConnection() {
    const statusDot = document.getElementById('statusDot');
    const statusText = document.getElementById('statusText');
    
    try {
        const response = await fetch(`${CONFIG.API_URL}/api/status`);
        if (response.ok) {
            const data = await response.json();
            statusDot.classList.remove('disconnected');
            statusText.textContent = '已连接';
            return true;
        }
    } catch (error) {
        console.error('Connection failed:', error);
    }
    
    statusDot.classList.add('disconnected');
    statusText.textContent = '未连接 - 请启动 Web UI 服务器';
    return false;
}

// 初始化会话
async function initSession() {
    currentSession = currentChatId;
    console.log('Session initialized:', currentSession);
}

// ========== 高危操作检测辅助函数 ==========

/**
 * 检查响应是否为高危操作确认响应
 * @param {Response} response - fetch响应对象
 * @returns {Object|null} 如果是高危操作响应返回解析后的JSON数据，否则返回null
 */
async function checkDangerResponse(response) {
    const contentType = response.headers.get('content-type') || '';
    if (contentType.includes('application/json') && !contentType.includes('text/event-stream')) {
        const jsonData = await response.json();
        if (jsonData.danger_check_required) {
            return jsonData;
        }
    }
    return null;
}

/**
 * 显示高危操作确认弹窗
 * @param {Object} jsonData - 后端返回的高危操作数据
 * @param {string} message - 用户输入的命令
 * @param {HTMLElement} messageDiv - 可选，需要移除的消息元素
 */
function showDangerModal(jsonData, message, messageDiv = null) {
    console.log('[DangerCheck] 检测到高危操作，显示确认弹窗');
    
    if (messageDiv) {
        messageDiv.remove();
    }
    
    if (typeof DangerModal !== 'undefined' && DangerModal.showApprovalRequest) {
        DangerModal.showApprovalRequest({
            request_id: 'danger_' + Date.now(),
            danger_level: jsonData.danger_match.level,
            category: jsonData.danger_match.category,
            description: jsonData.danger_match.description,
            suggestion: jsonData.danger_match.suggestion,
            command: message,
            timeout: 60
        });
    } else {
        alert(`高危操作警告: ${jsonData.danger_match.description}\n\n建议: ${jsonData.danger_match.suggestion}`);
    }
    
    hideTypingIndicator();
    isProcessing = false;
    updateSendButton();
}

/**
 * 处理高危操作检测的通用逻辑
 * @param {Response} response - fetch响应对象
 * @param {string} message - 用户输入的命令
 * @param {HTMLElement} messageDiv - 可选，需要移除的消息元素
 * @returns {boolean} 如果是高危操作返回true，否则返回false
 */
async function handleDangerCheck(response, message, messageDiv = null) {
    try {
        const dangerData = await checkDangerResponse(response);
        if (dangerData) {
            showDangerModal(dangerData, message, messageDiv);
            return true;
        }
    } catch (error) {
        console.error('[DangerCheck] 检查高危操作失败:', error);
    }
    return false;
}

// ========== 发送消息路由 ==========

// 发送消息 - 根据选择的模型自动路由
async function sendMessage() {
    // 根据selectedModel决定路由
    const modelType = getModelType(selectedModel);
    console.log('════════════════════════════════════════');
    console.log('[ModelSelect] 验证信息:');
    console.log('  选择的模型:', selectedModel);
    console.log('  模型类型:', modelType);
    console.log('  路由目标:', getRouteTarget(modelType));
    console.log('════════════════════════════════════════');
    
    switch (modelType) {
        case 'snn-neuracore':
            return sendViaNeuraCore();
        case 'snn-braintransformers':
            return sendViaBrainTransformers();
        case 'snn-spikegpt':
            return sendViaSpikeGPT();
        case 'ollama':
        default:
            // Ollama模型使用标准API
            return sendViaOllama();
    }
}

// 获取路由目标描述
function getRouteTarget(modelType) {
    const routes = {
        'ollama': '/api/chat/stream (Ollama后端)',
        'snn-neuracore': '/api/neuracore/stream (NeuraCore3M)',
        'snn-braintransformers': '/api/neuracore/stream (BrainTransformers-3B)',
        'snn-spikegpt': '/api/neuracore/stream (SpikeGPT)'
    };
    return routes[modelType] || '未知路由';
}

// 获取模型类型
function getModelType(model) {
    const snnModels = {
        'braintransformers-3b': 'snn-braintransformers',
        'neuracore3m': 'snn-neuracore',
        'spikegpt': 'snn-spikegpt'
    };
    return snnModels[model] || 'ollama';
}

// Ollama模型发送（标准API）
async function sendViaOllama() {
    const input = document.getElementById('messageInput');
    const message = input.value.trim();
    const pendingUploads = selectedAttachments
        .map((attachment) => attachment.uploadPromise)
        .filter(Boolean);
    if (pendingUploads.length) {
        await Promise.allSettled(pendingUploads);
    }
    const attachmentsForMessage = await Promise.all(selectedAttachments.map(async (attachment) => {
        let url = attachment.url || '';
        let dataUrl = attachment.dataUrl || '';
        let content = null;
        if (isTextPreviewable(attachment.type, attachment.name) && attachment.file) {
            try {
                content = await attachment.file.text();
            } catch (error) {
                console.warn('[sendViaOllama] Unable to read attachment content:', error);
            }
        }
        if (!url && isImagePreviewable(attachment.type, attachment.name) && attachment.file) {
            try {
                dataUrl = await fileToDataUrl(attachment.file);
            } catch (error) {
                console.warn('[sendViaOllama] Unable to read image data URL:', error);
            }
        }
        attachment.content = content || attachment.content || null;
        attachment.dataUrl = dataUrl || attachment.dataUrl || '';
        const payload = {
            name: attachment.name,
            type: attachment.type,
            url,
            size: attachment.file?.size || null,
            content,
            dataUrl
        };
        cacheAttachmentContent({ ...payload, content: content || '' });
        return payload;
    }));
    const preservedBlobUrls = new Set(
        attachmentsForMessage
            .map((attachment) => attachment.url)
            .filter((url) => url && url.startsWith('blob:'))
    );
    
    if ((!message && attachmentsForMessage.length === 0) || isProcessing) return;
    
    // 清空输入框
    input.value = '';
    input.style.height = 'auto';
    if (selectedAttachments.length) {
        selectedAttachments.forEach((attachment) => {
            if (attachment.previewUrl && !preservedBlobUrls.has(attachment.previewUrl)) {
                URL.revokeObjectURL(attachment.previewUrl);
            }
        });
        selectedAttachments = [];
        renderAttachments();
    }
    
    // 添加用户消息到界面
    addMessageToUI('user', message, false, false, attachmentsForMessage);
    
    // 添加到历史记录
    messageHistory.push({ role: 'user', content: message, attachments: attachmentsForMessage });
    messageHistory = normalizeChatHistory(messageHistory);
    // 立即保存，确保刷新/重开时可恢复会话与流式状态
    saveChatToHistory();
    
    // 显示输入中提示
    showTypingIndicator();
    
    // 发送给后端
    if (!currentSession) {
        currentSession = currentChatId;
    }
    isProcessing = true;
    updateSendButton();
    
    try {
        // 调用Ollama流式API
        await sendViaAPI(message, attachmentsForMessage);
    } catch (error) {
        console.error('Ollama发送失败:', error);
        addMessageToUI('bot', `❌ 发送失败: ${error.message}`);
    } finally {
        hideTypingIndicator();
        isProcessing = false;
        processingStartTime = null;
        updateSendButton();
    }
}

// BrainTransformers-3B 发送
async function sendViaBrainTransformers() {
    const input = document.getElementById('messageInput');
    const message = input.value.trim();
    
    if (!message || isProcessing) return;
    
    input.value = '';
    input.style.height = 'auto';
    
    addMessageToUI('user', message);
    messageHistory.push({ role: 'user', content: message });
    saveChatToHistory();
    showTypingIndicator();
    
    isProcessing = true;
    processingStartTime = Date.now();
    updateSendButton();
    
    try {
        const response = await fetch(`${CONFIG.API_URL}/api/neuracore/stream`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: message,
                session_id: currentChatId,
                mode: 'braintransformers',
                history: messageHistory.slice(-10)
            })
        });
        
        // 检查高危操作
        const streamingMsg = document.getElementById('streamingMessage');
        if (await handleDangerCheck(response, message, streamingMsg)) {
            return;
        }
        
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let fullResponse = '';
        let responseStats = null;
        let sseBuffer = '';
        
        while (true) {
            const { done, value } = await reader.read();
            if (done) {
                if (sseBuffer.trim().startsWith('data: ')) {
                    try {
                        const data = JSON.parse(sseBuffer.trim().slice(6));
                        if (data.type === 'done') { fullResponse = data.response || fullResponse; }
                    } catch (e) {}
                }
                break;
            }
            
            sseBuffer += decoder.decode(value, { stream: true });
            const lines = sseBuffer.split('\n');
            sseBuffer = lines.pop() || '';
            
            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    try {
                        const data = JSON.parse(line.slice(6));
                        if (data.type === 'chunk') {
                            fullResponse += data.content;
                            updateStreamingMessage(fullResponse);
                        } else if (data.type === 'done') {
                            fullResponse = data.response || fullResponse;
                        }
                    } catch (e) {}
                }
            }
        }
        
        messageHistory.push({ role: 'assistant', content: fullResponse });
        saveChatToHistory();
        finalizeStreamingMessage(fullResponse);
        
    } catch (error) {
        console.error('BrainTransformers发送失败:', error);
        addMessageToUI('bot', `❌ BrainTransformers错误: ${error.message}`);
    } finally {
        hideTypingIndicator();
        isProcessing = false;
        processingStartTime = null;
        updateSendButton();
    }
}

// SpikeGPT 发送
async function sendViaSpikeGPT() {
    const input = document.getElementById('messageInput');
    const message = input.value.trim();
    
    if (!message || isProcessing) return;
    
    input.value = '';
    input.style.height = 'auto';
    
    addMessageToUI('user', message);
    messageHistory.push({ role: 'user', content: message });
    saveChatToHistory();
    showTypingIndicator();
    
    isProcessing = true;
    updateSendButton();
    
    try {
        const response = await fetch(`${CONFIG.API_URL}/api/neuracore/stream`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: message,
                session_id: currentChatId,
                mode: 'spikegpt',
                history: messageHistory.slice(-10)
            })
        });
        
        // 检查高危操作
        const streamingMsg = document.getElementById('streamingMessage');
        if (await handleDangerCheck(response, message, streamingMsg)) {
            return;
        }
        
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let fullResponse = '';
        let sseBuffer = '';
        
        while (true) {
            const { done, value } = await reader.read();
            if (done) {
                if (sseBuffer.trim().startsWith('data: ')) {
                    try {
                        const data = JSON.parse(sseBuffer.trim().slice(6));
                        if (data.type === 'done') { fullResponse = data.response || fullResponse; }
                    } catch (e) {}
                }
                break;
            }
            
            sseBuffer += decoder.decode(value, { stream: true });
            const lines = sseBuffer.split('\n');
            sseBuffer = lines.pop() || '';
            
            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    try {
                        const data = JSON.parse(line.slice(6));
                        if (data.type === 'chunk') {
                            fullResponse += data.content;
                            updateStreamingMessage(fullResponse);
                        } else if (data.type === 'done') {
                            fullResponse = data.response || fullResponse;
                        }
                    } catch (e) {}
                }
            }
        }
        
        messageHistory.push({ role: 'assistant', content: fullResponse });
        saveChatToHistory();
        finalizeStreamingMessage(fullResponse);
        
    } catch (error) {
        console.error('SpikeGPT发送失败:', error);
        addMessageToUI('bot', `❌ SpikeGPT错误: ${error.message}`);
    } finally {
        hideTypingIndicator();
        isProcessing = false;
        processingStartTime = null;
        updateSendButton();
    }
}

// 更新流式消息内容
function updateStreamingMessage(content) {
    const streamingDiv = document.getElementById('streamingMessage');
    if (!streamingDiv) {
        resetStreamingMarkdownState();
        // 创建流式消息元素
        const messagesDiv = document.getElementById('messages');
        const newDiv = document.createElement('div');
        newDiv.className = 'message bot streaming';
        newDiv.id = 'streamingMessage';
        newDiv.innerHTML = `
            <div class="avatar">
                <i class="fas fa-robot"></i>
            </div>
            <div class="message-content">
                <div class="streaming-content" style="white-space: pre-wrap; word-wrap: break-word;"></div>
            </div>
        `;
        messagesDiv.appendChild(newDiv);
    }
    
    const contentDiv = document.querySelector('#streamingMessage .streaming-content');
    if (contentDiv) {
        const messagesDiv = document.getElementById('messages');
        const shouldStickToBottom = shouldAutoScroll(messagesDiv);
        renderStreamingMarkdown(contentDiv, content);
        if (shouldStickToBottom) {
            scrollToBottom();
        }
    }
}

// 完成流式消息
function finalizeStreamingMessage(content) {
    const streamingDiv = document.getElementById('streamingMessage');
    if (streamingDiv) {
        streamingDiv.classList.remove('streaming');
        streamingDiv.removeAttribute('id');
        const contentDiv = streamingDiv.querySelector('.streaming-content');
        if (contentDiv) {
            cancelStreamingMarkdownRender();
            try {
                contentDiv.innerHTML = processMarkdown(content);
            } catch (e) {
                contentDiv.textContent = content;
            }
        }
        resetStreamingMarkdownState();
        // Quick-reply buttons for confirmation questions
        renderQuickReplies(streamingDiv, content);
    }
}

// ========== Quick Reply Buttons ==========
// Detect confirmation questions / choices in bot responses and render clickable buttons

/**
 * Detect if a bot message ends with a question that has actionable choices.
 * Returns an array of {label, value} objects, or empty array if no choices detected.
 */
function detectQuickReplies(content) {
    if (!content || content.length < 10) return [];
    
    // Strip markdown formatting for cleaner pattern matching
    const stripped = content
        .replace(/\*\*(.+?)\*\*/g, '$1')   // **bold**
        .replace(/\*(.+?)\*/g, '$1')        // *italic*
        .replace(/`([^`]+)`/g, '$1')        // `code`
        .replace(/#{1,6}\s*/g, '')           // headers
        .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1'); // [link](url)
    
    // Take last 800 chars for pattern matching (question is usually at the end)
    const tail = stripped.slice(-800);
    const replies = [];
    
    console.log('[QuickReply] Scanning tail (last 120 chars):', JSON.stringify(tail.slice(-120)));
    
    // Pattern 1: Numbered options like "1. 执行两项操作  2. 仅执行第一项  3. 取消"
    const numberedOpts = tail.match(/(?:^|\n)\s*(\d+)[.)]\s*(.{2,60})(?=\s*\n\s*\d+[.)]|\s*$)/gm);
    if (numberedOpts && numberedOpts.length >= 2) {
        for (const opt of numberedOpts) {
            const m = opt.match(/(\d+)[.)]\s*(.+)/);
            if (m) {
                replies.push({ label: m[2].trim(), value: m[1] + '. ' + m[2].trim() });
            }
        }
        if (replies.length >= 2) return replies.slice(0, 4);
    }
    
    // Pattern 2: "请确认" anywhere near the end (with or without question mark, with period)
    // Matches: "请确认您已知晓上述风险并同意执行。" / "请确认是否继续？"
    const pleaseConfirm = /请(?:确认|选择|决定).{0,40}[。？?!]?\s*$/;
    if (pleaseConfirm.test(tail)) {
        console.log('[QuickReply] Matched: 请确认 pattern');
        return [
            { label: '✅ 确认执行', value: '确认，请继续执行' },
            { label: '❌ 取消', value: '取消，不要执行' }
        ];
    }
    
    // Pattern 3: Yes/No confirmation questions (Chinese) ending with ？
    // "是否确认" "是否要执行" "是否继续" "确认执行吗" "要继续吗"
    const confirmQuestion = /(?:是否|要不要|需不需要|可以吗|确认.*吗|继续.*吗|要.*吗).{0,30}[？?]\s*$/;
    if (confirmQuestion.test(tail)) {
        console.log('[QuickReply] Matched: 确认问句 pattern');
        return [
            { label: '✅ 确认执行', value: '确认，请继续执行' },
            { label: '❌ 取消', value: '取消，不要执行' }
        ];
    }
    
    // Pattern 4: Sentences ending with confirmation keywords + period/question
    // "需要您确认后才能执行" "同意执行" "确认后再执行"
    const confirmStatement = /(?:确认后|同意后|同意执行|确认.*执行|需要.*确认|等待.*确认|awaiting.*confirm)[^。？?]{0,20}[。？?]?\s*$/i;
    if (confirmStatement.test(tail)) {
        console.log('[QuickReply] Matched: 确认声明 pattern');
        return [
            { label: '✅ 确认，执行', value: '确认，请继续执行' },
            { label: '❌ 不执行', value: '取消，不要执行' }
        ];
    }
    
    // Pattern 5: English yes/no "Would you like to proceed?" "Should I continue?"
    const engConfirm = /(?:would you like|should I|shall I|do you want|proceed|continue|confirm)\s*[.?]?\s*$/i;
    if (engConfirm.test(tail)) {
        console.log('[QuickReply] Matched: English confirm pattern');
        return [
            { label: '✅ Yes, proceed', value: 'Yes, please proceed.' },
            { label: '❌ No, cancel', value: 'No, cancel.' }
        ];
    }
    
    // Pattern 6: "A 还是 B" / "A or B" choice
    const orPattern = tail.match(/["""「](.{2,30})["""」]\s*(?:还是|或者|or)\s*["""「](.{2,30})["""」]\s*[？?]?\s*$/i);
    if (orPattern) {
        console.log('[QuickReply] Matched: A还是B pattern');
        return [
            { label: orPattern[1].trim(), value: orPattern[1].trim() },
            { label: orPattern[2].trim(), value: orPattern[2].trim() }
        ];
    }
    
    console.log('[QuickReply] No patterns matched');
    return [];
}

/**
 * Render quick-reply buttons below a bot message div.
 * When clicked, sends the choice as a new user message.
 */
function renderQuickReplies(messageDiv, content) {
    const replies = detectQuickReplies(content);
    if (replies.length === 0) return;
    
    // Remove any existing quick-reply bar
    const existing = messageDiv.querySelector('.quick-reply-bar');
    if (existing) existing.remove();
    
    const bar = document.createElement('div');
    bar.className = 'quick-reply-bar';
    
    const label = document.createElement('span');
    label.className = 'quick-reply-label';
    label.textContent = '快速回复:';
    bar.appendChild(label);
    
    for (const reply of replies) {
        const btn = document.createElement('button');
        btn.className = 'quick-reply-btn';
        btn.textContent = reply.label;
        btn.title = reply.value;
        btn.addEventListener('click', () => {
            // Remove all quick-reply bars (one-shot)
            document.querySelectorAll('.quick-reply-bar').forEach(el => el.remove());
            // Send the choice as a new user message
            const input = document.getElementById('messageInput');
            if (input) {
                input.value = reply.value;
                sendMessage();
            }
        });
        bar.appendChild(btn);
    }
    
    // Insert into the message-content area, at the bottom
    const msgContent = messageDiv.querySelector('.message-content');
    if (msgContent) {
        msgContent.appendChild(bar);
    } else {
        messageDiv.appendChild(bar);
    }
    
    console.log(`[QuickReply] Rendered ${replies.length} buttons for message`);
}

function cancelStreamingMarkdownRender() {
    if (streamingMarkdownState.rafId !== null) {
        cancelAnimationFrame(streamingMarkdownState.rafId);
        streamingMarkdownState.rafId = null;
    }
}

function resetStreamingMarkdownState() {
    cancelStreamingMarkdownRender();
    streamingMarkdownState.pendingContent = null;
    streamingMarkdownState.lastRenderedContent = '';
    streamingMarkdownState.lastRenderedHtml = '';
    streamingMarkdownState.markdownRenderState = createMarkdownBlockCacheState();
}

function renderStreamingMarkdownImmediate(contentDiv, content, cacheContainer = null) {
    if (!contentDiv) return;
    const renderState = cacheContainer
        ? getMarkdownBlockCacheState(cacheContainer)
        : streamingMarkdownState.markdownRenderState;
    try {
        contentDiv.innerHTML = processMarkdown(content, { renderState });
    } catch (error) {
        contentDiv.textContent = content;
    }
}

function renderStreamingMarkdown(contentDiv, content) {
    if (!contentDiv) return;

    // 相同内容直接复用缓存结果
    if (content === streamingMarkdownState.lastRenderedContent && streamingMarkdownState.lastRenderedHtml) {
        streamingMarkdownState.metrics.cachedFrameHits += 1;
        contentDiv.innerHTML = streamingMarkdownState.lastRenderedHtml;
        return;
    }

    streamingMarkdownState.pendingContent = content;
    if (streamingMarkdownState.rafId !== null) return;

    const flush = () => {
        streamingMarkdownState.rafId = null;
        const nextContent = streamingMarkdownState.pendingContent;
        streamingMarkdownState.pendingContent = null;

        if (nextContent == null) return;

        try {
            const html = processMarkdown(nextContent, {
                renderState: streamingMarkdownState.markdownRenderState
            });
            streamingMarkdownState.lastRenderedContent = nextContent;
            streamingMarkdownState.lastRenderedHtml = html;
            streamingMarkdownState.metrics.frameRenders += 1;
            contentDiv.innerHTML = html;
            if (isMarkdownDebugEnabled() && streamingMarkdownState.metrics.frameRenders % 20 === 0) {
                const m = streamingMarkdownState.metrics;
                const totalFrames = m.frameRenders + m.cachedFrameHits;
                const cacheRate = totalFrames > 0
                    ? ((m.cachedFrameHits / totalFrames) * 100).toFixed(1)
                    : '0.0';
                markdownDebugLog(
                    `stream frames rendered=${m.frameRenders}`,
                    `cachedHits=${m.cachedFrameHits}`,
                    `cacheRate=${cacheRate}%`
                );
            }
        } catch (error) {
            contentDiv.textContent = nextContent;
        }

        if (streamingMarkdownState.pendingContent !== null) {
            streamingMarkdownState.rafId = requestAnimationFrame(flush);
        }
    };

    streamingMarkdownState.rafId = requestAnimationFrame(flush);
}

function handleSendOrStop(event) {
    if (event && typeof event.preventDefault === 'function') {
        event.preventDefault();
    }
    if (event && typeof event.stopPropagation === 'function') {
        event.stopPropagation();
    }
    const targetSession = currentSession || currentChatId;
    const streamingStateRaw = localStorage.getItem(`streaming_${targetSession}`);
    let isSessionStreaming = false;
    if (streamingStateRaw) {
        try {
            const state = JSON.parse(streamingStateRaw);
            isSessionStreaming = state && state.isStreaming === true;
        } catch (e) {
            isSessionStreaming = false;
        }
    }

    // 只要当前会话仍在 streaming，就允许触发 stop（即使 isProcessing 因断线/abort 提前变为 false）
    if (isProcessing || isSessionStreaming) {
        stopStreaming();
        return;
    }
    sendMessage();
}

async function stopStreaming() {
    const targetSession = currentSession || currentChatId;
    const streamingStateRaw = localStorage.getItem(`streaming_${targetSession}`);
    let isSessionStreaming = false;
    if (streamingStateRaw) {
        try {
            const state = JSON.parse(streamingStateRaw);
            isSessionStreaming = state && state.isStreaming === true;
        } catch (e) {
            isSessionStreaming = false;
        }
    }
    if (!isProcessing && !isSessionStreaming) {
        console.log('[stopStreaming] No active processing/streaming, ignore');
        return;
    }
    stopRequested = true;
    console.log('[stopStreaming] Sending stop request for session:', targetSession);
    updateSendButton();
    if (currentAbortController) {
        currentAbortController.abort();
    }
    try {
        await fetch(`${CONFIG.API_URL}/api/chat/stop`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ session_id: targetSession })
        });
    } catch (error) {
        console.warn('[stopStreaming] Failed to notify backend:', error);
    } finally {
        // 立即刷新按钮显示（真正完成会由 streaming poll 清理 streaming_ state 后切回发送）
        updateSendButton();
    }
}

// SNN模式切换
function toggleSNNMode() {
    // 关闭其他模式
    snnModeAEnabled = false;
    snnModeBEnabled = false;
    neuraCoreEnabled = false;
    snnModeEnabled = !snnModeEnabled;
    
    updateAllSNNButtons();
    console.log('[toggleSNNMode] SNN模式:', snnModeEnabled ? '启用' : '禁用');
}

// SNN模式A切换 (工具调用)
function toggleSNNModeA() {
    snnModeEnabled = false;
    snnModeBEnabled = false;
    neuraCoreEnabled = false;
    snnModeAEnabled = !snnModeAEnabled;
    
    updateAllSNNButtons();
    if (snnModeAEnabled) {
        showNotification('深度脉冲A: SNN+工具调用模式', 'success');
    }
    console.log('[toggleSNNModeA] 深度脉冲A:', snnModeAEnabled ? '启用' : '禁用');
}

// SNN模式B切换 (Agent整合)
function toggleSNNModeB() {
    snnModeEnabled = false;
    snnModeAEnabled = false;
    neuraCoreEnabled = false;
    snnModeBEnabled = !snnModeBEnabled;
    
    updateAllSNNButtons();
    if (snnModeBEnabled) {
        showNotification('深度脉冲B: SNN+Agent系统模式', 'success');
    }
    console.log('[toggleSNNModeB] 深度脉冲B:', snnModeBEnabled ? '启用' : '禁用');
}

// NeuraCore神经核心切换（支持BrainTransformers-3B高质量模式）
function toggleNeuraCore() {
    snnModeEnabled = false;
    snnModeAEnabled = false;
    snnModeBEnabled = false;
    neuraCoreEnabled = !neuraCoreEnabled;
    
    // 如果启用NeuraCore，默认启用BrainTransformers-3B高质量模式
    if (neuraCoreEnabled) {
        spikeGPTModeEnabled = true;  // 现在控制BrainTransformers-3B
    } else {
        spikeGPTModeEnabled = false;
    }
    
    updateAllSNNButtons();
    if (neuraCoreEnabled) {
        showNotification('NeuraCore神经核心: BrainTransformers-3B模式已激活 (3.1B参数高质量SNN)', 'success');
    } else {
        showNotification('NeuraCore神经核心已关闭', 'info');
    }
    console.log('[toggleNeuraCore] NeuraCore:', neuraCoreEnabled ? '启用' : '禁用', 'BrainTransformers-3B模式:', spikeGPTModeEnabled);
}

// 更新所有SNN按钮状态
function updateAllSNNButtons() {
    const btn = document.getElementById('neuraCoreBtn');
    const btnA = document.getElementById('snnModeABtn');
    const btnB = document.getElementById('snnModeBBtn');
    
    if (btn) {
        btn.classList.toggle('active', neuraCoreEnabled);
        if (neuraCoreEnabled && spikeGPTModeEnabled) {
            btn.title = 'BrainTransformers-3B模式已启用 (3.1B参数高质量) (点击关闭)';
            // 添加视觉指示器显示BrainTransformers-3B模式
            btn.innerHTML = '<i class="fas fa-brain"></i><span class="tool-name">🧠BrainTrans-3B</span>';
        } else if (neuraCoreEnabled) {
            btn.title = 'NeuraCore神经核心已启用 - 原生脉冲语言模式 (点击关闭)';
            btn.innerHTML = '<i class="fas fa-brain"></i><span class="tool-name">NeuraCore</span>';
        } else {
            btn.title = 'NeuraCore神经核心 - BrainTransformers-3B (3.1B参数SNN)';
            btn.innerHTML = '<i class="fas fa-brain"></i>';
        }
    }
    if (btnA) {
        btnA.classList.toggle('active', snnModeAEnabled);
        btnA.title = snnModeAEnabled ? '深度脉冲A已启用 (点击关闭)' : '深度脉冲A - SNN+工具调用';
    }
    if (btnB) {
        btnB.classList.toggle('active', snnModeBEnabled);
        btnB.title = snnModeBEnabled ? '深度脉冲B已启用 (点击关闭)' : '深度脉冲B - SNN+Agent系统';
    }
}

// 检查SNN状态
async function checkSNNStatus() {
    try {
        const response = await fetch(`${CONFIG.API_URL}/api/snn/status`);
        const data = await response.json();
        console.log('[SNN状态]', data);
        if (!data.enabled) {
            showNotification('SNN系统未就绪，正在初始化...', 'warning');
        } else {
            showNotification(`SNN已激活: ${data.stats.total_questions}问题已处理`, 'success');
        }
    } catch (error) {
        console.error('[SNN状态检查失败]', error);
        showNotification('SNN状态检查失败', 'error');
    }
}

// 通过SNN-LLM发送消息
async function sendViaSNN() {
    const input = document.getElementById('messageInput');
    const message = input.value.trim();
    
    if (!message) {
        return;
    }
    
    // 添加用户消息到界面和历史
    addMessageToUI('user', message, false, false, []);
    messageHistory.push({ role: 'user', content: message });
    input.value = '';
    
    // 显示处理中
    const processingDiv = addMessageToUI('assistant', '🧠 SNN处理中...', false, false, []);
    
    isProcessing = true;
    updateSendButton();
    
    try {
        const response = await fetch(`${CONFIG.API_URL}/api/chat/snn`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: message,
                session_id: currentSession || currentChatId
            })
        });
        
        // 检查高危操作
        if (await handleDangerCheck(response, message, processingDiv)) {
            return;
        }
        
        const data = await response.json();
        
        // 移除处理中消息
        if (processingDiv) {
            processingDiv.remove();
        }
        
        if (data.success) {
            // 显示SNN统计
            const stats = data.snn_stats;
            const statsHtml = `
                <div class="snn-stats" style="font-size: 12px; color: #8b5cf6; margin-bottom: 10px; padding: 8px; background: rgba(139, 92, 246, 0.1); border-radius: 6px;">
                    <span>⚡ ${stats.spikes}脉冲</span>
                    <span style="margin-left: 10px;">🎯 注意力: ${stats.attention.toFixed(2)}</span>
                    <span style="margin-left: 10px;">⏱️ ${stats.processing_time.toFixed(1)}s</span>
                </div>
            `;
            const fullResponse = statsHtml + data.response;
            addMessageToUI('assistant', fullResponse, true, false, []);
            // 保存到历史
            messageHistory.push({ role: 'assistant', content: fullResponse, snn_mode: 'basic' });
        } else {
            addMessageToUI('assistant', '❌ ' + data.response, true, false, []);
            messageHistory.push({ role: 'assistant', content: '❌ ' + data.response });
        }
        
        // 保存聊天历史
        saveChatToHistory();
        
        // 滚动到底部
        scrollToBottom();
        
    } catch (error) {
        console.error('[SNN发送失败]', error);
        if (processingDiv) {
            processingDiv.remove();
        }
        addMessageToUI('assistant', '❌ SNN处理失败: ' + error.message, true, false, []);
        messageHistory.push({ role: 'assistant', content: '❌ SNN处理失败: ' + error.message });
        saveChatToHistory();
    } finally {
        isProcessing = false;
        updateSendButton();
    }
}

// 深度脉冲A: SNN + 工具调用
async function sendViaSNNA() {
    const input = document.getElementById('messageInput');
    const message = input.value.trim();
    
    if (!message) {
        return;
    }
    
    // 添加用户消息到界面和历史
    addMessageToUI('user', message, false, false, []);
    messageHistory.push({ role: 'user', content: message });
    input.value = '';
    
    // 创建流式消息容器
    const streamingMsgId = 'stream-' + Date.now();
    const streamingDiv = addMessageToUI('assistant', '🧠 SNN预处理中...', true, true, []);
    streamingDiv.id = streamingMsgId;
    
    isProcessing = true;
    processingStartTime = Date.now();  // 记录开始时间
    updateSendButton();
    
    let fullResponse = '';
    let snnStats = null;
    
    try {
        const response = await fetch(`${CONFIG.API_URL}/api/chat/snn/a/stream`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: message,
                session_id: currentSession || currentChatId
            })
        });
        
        // 检查高危操作
        if (await handleDangerCheck(response, message, streamingDiv)) {
            return;
        }
        
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            
            const chunk = decoder.decode(value);
            const lines = chunk.split('\n');
            
            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    const dataStr = line.slice(6);
                    if (dataStr === '[DONE]') {
                        continue;
                    }
                    
                    try {
                        const data = JSON.parse(dataStr);
                        
                        if (data.type === 'snn_stats') {
                            snnStats = data.stats;
                            // 仅在SNN相关问题或调试模式时显示统计
                            const isSNNQuery = message.toLowerCase().includes('snn') || 
                                               message.toLowerCase().includes('脉冲') ||
                                               message.toLowerCase().includes('神经网络') ||
                                               message.toLowerCase().includes('spike');
                            const isTestQuery = message.toLowerCase().includes('测试') ||
                                               message.toLowerCase().includes('test');
                            
                            // 测试类查询不显示SNN统计，保持回答相关性
                            if (!isTestQuery && isSNNQuery) {
                                // 检查是否已经有snn-stats，避免重复添加
                                const existingStats = streamingDiv.querySelector('.snn-stats');
                                if (!existingStats) {
                                    const statsHtml = `
                                        <div class="snn-stats" style="font-size: 12px; color: #10b981; margin-bottom: 10px; padding: 8px; background: rgba(16, 185, 129, 0.1); border-radius: 6px;">
                                            <span>⚡ ${snnStats.spikes}脉冲</span>
                                            <span style="margin-left: 10px;">🎯 注意力: ${snnStats.attention.toFixed(2)}</span>
                                            <span style="margin-left: 10px;">⏱️ ${snnStats.processing_time.toFixed(1)}s</span>
                                        </div>
                                    `;
                                    const msgContent = streamingDiv.querySelector('.message-content');
                                    if (msgContent) {
                                        msgContent.insertAdjacentHTML('afterbegin', statsHtml);
                                    }
                                }
                            }
                        } else if (data.type === 'tools') {
                            // 工具执行状态 - 使用insertAdjacentHTML避免重置内容
                            const existingToolStatus = streamingDiv.querySelector('.tool-status');
                            if (!existingToolStatus) {
                                const toolsHtml = `
                                    <div class="tool-status" style="font-size: 12px; color: #f59e0b; margin-bottom: 10px; padding: 8px; background: rgba(245, 158, 11, 0.1); border-radius: 6px;">
                                        <span>🔧 已执行工具: ${data.tools.join(', ')}</span>
                                    </div>
                                `;
                                const msgContent = streamingDiv.querySelector('.message-content');
                                if (msgContent) {
                                    msgContent.insertAdjacentHTML('afterbegin', toolsHtml);
                                }
                            }
                        } else if (data.type === 'chunk') {
                            fullResponse += data.content;
                            // 实时更新内容 - 轻度清理，保留Markdown格式
                            const parsed = parseThinking(fullResponse);
                            // 只将4个及以上换行压缩为2个（保留段落间距）
                            let text = parsed.text.replace(/\n{4,}/g, '\n\n');
                            // 不压缩列表项之间的换行，保留Markdown列表格式
                            const safeStreamingText = text;
                            
                            // 判断是否显示SNN统计
                            const isSNNQuery = message.toLowerCase().includes('snn') || 
                                               message.toLowerCase().includes('脉冲') ||
                                               message.toLowerCase().includes('神经网络');
                            const isTestQuery = message.toLowerCase().includes('测试') ||
                                               message.toLowerCase().includes('test');
                            
                            // 测试类查询不显示SNN统计，保持回答相关性
                            const statsHtml = (snnStats && isSNNQuery && !isTestQuery) ? `
                                <div class="snn-stats" style="font-size: 14px; color: #10b981; margin-bottom: 8px; padding: 6px 10px; background: rgba(16, 185, 129, 0.1); border-radius: 4px; display: flex; flex-wrap: wrap; gap: 8px;">
                                    <span>⚡${snnStats.spikes}脉冲</span><span>🎯注意力:${snnStats.attention.toFixed(2)}</span><span>⏱️${snnStats.processing_time.toFixed(1)}s</span>
                                </div>
                            ` : '';

                            renderStreamingFrame(streamingDiv, {
                                icon: 'fa-robot',
                                prefixHtml: statsHtml,
                                markdownContent: safeStreamingText
                            });
                            if (shouldAutoScroll()) scrollToBottom();
                        } else if (data.type === 'cached') {
                            // 缓存命中
                            fullResponse = data.response;
                            snnStats = data.snn_stats;
                            const statsHtml = `
                                <div class="snn-stats" style="font-size: 14px; color: #10b981; margin-bottom: 8px; padding: 6px 10px; background: rgba(16, 185, 129, 0.1); border-radius: 4px; display: flex; flex-wrap: wrap; gap: 8px;">
                                    <span>⚡${snnStats.spikes}脉冲</span><span>🎯注意力:${snnStats.attention.toFixed(2)}</span><span>📦缓存命中</span>
                                </div>
                            `;
                            renderStreamingFrame(streamingDiv, {
                                icon: 'fa-robot',
                                prefixHtml: statsHtml,
                                markdownContent: fullResponse
                            });
                        } else if (data.error) {
                            streamingDiv.innerHTML = `
                                <div class="avatar"><i class="fas fa-robot"></i></div>
                                <div class="message-content">❌ 错误: ${data.error}</div>
                            `;
                        } else if (data.type === 'quality') {
                            // 质量评分（单行紧凑格式）
                            const score = data.score;
                            const details = data.details;
                            const qualityHtml = `
                                <div class="quality-score" style="font-size: 14px; color: #6b7280; margin-top: 8px; padding: 6px 10px; background: rgba(107, 114, 128, 0.1); border-radius: 4px; display: flex; flex-wrap: wrap; gap: 8px;">
                                    <span>📊质量:${(score * 100).toFixed(0)}分</span><span>长度:${(details.length * 100).toFixed(0)}%</span><span>结构:${(details.structure * 100).toFixed(0)}%</span><span>相关:${(details.relevance * 100).toFixed(0)}%</span>
                                </div>
                            `;
                            // 添加到消息内容
                            const msgContent = streamingDiv.querySelector('.message-content');
                            if (msgContent) {
                                msgContent.insertAdjacentHTML('beforeend', qualityHtml);
                            }
                        }
                    } catch (e) {
                        // JSON解析错误，忽略
                    }
                }
            }
        }
        
        // 完成
        streamingDiv.classList.remove('streaming');
        streamingDiv.removeAttribute('id');
        
        // 计算耗时
        const elapsedSeconds = processingStartTime ? ((Date.now() - processingStartTime) / 1000).toFixed(2) : '0.00';
        
        // 添加完成标识（带耗时）
        const completeDiv = document.createElement('div');
        completeDiv.className = 'response-complete';
        completeDiv.innerHTML = '<i class="fas fa-check-circle"></i><span>回答已完成</span><span class="elapsed-time">⏱️ 耗时: ' + elapsedSeconds + 's</span>';
        streamingDiv.querySelector('.message-content').appendChild(completeDiv);
        
        // 添加消息操作按钮（复制等）- 不包含SNN状态文本
        addMessageActions(streamingDiv, fullResponse, 'assistant');
        
        // 保存到历史（不包含SNN状态文本，保持相关性）
        messageHistory.push({ role: 'assistant', content: fullResponse, snn_mode: 'A' });
        saveChatToHistory();
        
    } catch (error) {
        console.error('[深度脉冲A发送失败]', error);
        streamingDiv.innerHTML = `
            <div class="avatar"><i class="fas fa-robot"></i></div>
            <div class="message-content">❌ 深度脉冲A处理失败: ${error.message}</div>
        `;
        messageHistory.push({ role: 'assistant', content: '❌ 深度脉冲A处理失败: ' + error.message });
        saveChatToHistory();
    } finally {
        isProcessing = false;
        updateSendButton();
    }
}

// 深度脉冲B: Nanobot V3 - 2.7M神经元OS智能体 (流式版本)
async function sendViaSNNB() {
    const input = document.getElementById('messageInput');
    const message = input.value.trim();
    
    if (!message) {
        return;
    }
    
    // 添加用户消息到界面和历史
    addMessageToUI('user', message, false, false, []);
    messageHistory.push({ role: 'user', content: message });
    saveChatToHistory();  // 立即保存，防止用户消息在刷新时丢失
    input.value = '';
    
    // 创建流式消息容器
    const streamingMsgId = 'stream-v3-' + Date.now();
    const streamingDiv = addMessageToUI('assistant', '🧠 V3初始化...', true, true, []);
    streamingDiv.id = streamingMsgId;
    
    isProcessing = true;
    processingStartTime = Date.now();  // 记录开始时间
    updateSendButton();
    
    let fullResponse = '';
    let v3Stats = null;
    let predictedCommand = null;
    
    try {
        // 获取当前会话的历史
        const savedMessages = localStorage.getItem(`chat_${currentChatId}`);
        const historyForAPI = savedMessages ? JSON.parse(savedMessages) : messageHistory;
        
        const response = await fetch(`${CONFIG.API_URL}/api/v3/os_agent/stream`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                context: message,
                history: (historyForAPI || []).slice(-10) // 传递最近10条作为上下文
            })
        });
        
        // 检查高危操作
        if (await handleDangerCheck(response, message, streamingDiv)) {
            return;
        }
        
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            
            const chunk = decoder.decode(value);
            const lines = chunk.split('\n');
            
            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    const dataStr = line.slice(6);
                    if (dataStr === '[DONE]') {
                        continue;
                    }
                    
                    try {
                        const data = JSON.parse(dataStr);
                        
                        if (data.type === 'start') {
                            // 初始化流式响应 - 显示思考中状态
                            renderStreamingStatusFrame(streamingDiv, 'start', data, {
                                icon: 'fa-robot',
                                messageStyle: 'margin-left: 10px; margin-right: 120px;',
                                statusPrefix: '🧠 ',
                                fallbackStatusText: '深度思考中...',
                                markdownContent: ''
                            });
                        } else if (data.type === 'chunk' || data.type === 'llm_chunk') {
                            // 增量内容 - 像V2那样累加显示
                            transitionStreamingLifecycle(streamingDiv, data.type);
                            fullResponse += data.content || '';
                            const contentDiv = streamingDiv.querySelector('.streaming-content');
                            const shouldStickToBottom = shouldAutoScroll();
                            if (contentDiv) {
                                renderStreamingMarkdownImmediate(contentDiv, fullResponse, streamingDiv);
                            }
                            if (shouldStickToBottom) scrollToBottom();
                        } else if (data.type === 'status') {
                            renderStreamingStatusFrame(streamingDiv, 'status', data, {
                                icon: 'fa-robot',
                                messageStyle: 'margin-left: 10px; margin-right: 120px;',
                                statusPrefix: '🧠 ',
                                markdownContent: fullResponse || ''
                            });
                        } else if (data.type === 'mode') {
                            renderStreamingStatusFrame(streamingDiv, 'mode', data, {
                                icon: 'fa-sliders',
                                messageStyle: 'margin-left: 10px; margin-right: 120px;',
                                statusPrefix: '🎛️ ',
                                markdownContent: fullResponse || ''
                            });
                            if (shouldAutoScroll()) scrollToBottom();
                        } else if (data.type === 'perception') {
                            // 感知阶段完成 - 显示脉冲统计但保留已有内容
                            v3Stats = v3Stats || {};
                            v3Stats.visual_spikes = data.visual_spikes;
                            v3Stats.semantic_spikes = data.semantic_spikes;
                            renderStreamingStatusFrame(streamingDiv, 'perception', {
                                message: `视觉:${data.visual_spikes}脉冲 | 语义:${data.semantic_spikes}脉冲 | SNN推理中...`
                            }, {
                                icon: 'fa-robot',
                                messageStyle: 'margin-left: 10px; margin-right: 120px;',
                                statusPrefix: '👁 ',
                                markdownContent: fullResponse || ''
                            });
                        } else if (data.type === 'cognition') {
                            // 认知阶段完成
                            v3Stats = v3Stats || {};
                            v3Stats.decision_spikes = data.decision_spikes;
                            v3Stats.dopamine = data.dopamine;
                            predictedCommand = data.command;
                            renderStreamingStatusFrame(streamingDiv, 'cognition', {
                                message: `${data.decision_spikes.toLocaleString()}脉冲 | 多巴胺:${data.dopamine.toFixed(3)} | 预测:${data.command}`
                            }, {
                                icon: 'fa-robot',
                                messageStyle: 'margin-left: 10px; margin-right: 120px;',
                                statusPrefix: '⚡ ',
                                markdownContent: fullResponse || ''
                            });
                        } else if (data.type === 'complete') {
                            // 完成 - 显示最终结果
                            transitionStreamingLifecycle(streamingDiv, 'complete');
                            v3Stats = data.stats;
                            fullResponse = data.response;

                            // 统一渲染主回答（块级缓存）
                            renderStreamingFrame(streamingDiv, {
                                icon: 'fa-robot',
                                messageStyle: 'margin-left: 10px; margin-right: 120px;',
                                markdownContent: fullResponse
                            });
                            
                            // 构建统计HTML（作为技术附录放在底部）
                            const totalSpikes = (v3Stats.sensory_spikes + v3Stats.association_spikes + v3Stats.decision_spikes + v3Stats.motor_spikes).toLocaleString();
                            const statsHtml = '<div class="snn-stats" style="font-size: 14px; color: #f59e0b; margin-top: 16px; padding: 8px 12px; background: rgba(245, 158, 11, 0.1); border-radius: 4px; display: flex; flex-wrap: wrap; gap: 8px; border-left: 3px solid #f59e0b;"><span style="font-weight: 600; width: 100%; margin-bottom: 4px;">📊 技术附录 | SNN脉冲统计</span><span>🧠6.0M神经元</span><span>⚡' + totalSpikes + '脉冲</span><span>🎯多巴胺:' + v3Stats.dopamine.toFixed(3) + '</span><span>⏱️' + data.elapsed_ms.toFixed(0) + 'ms</span></div>';
                            
                            // 如果有预测命令，显示它（放在主回答之后，技术附录之前）
                            let commandHtml = '';
                            if (predictedCommand && predictedCommand !== 'unknown') {
                                commandHtml = '<div style="font-size: 13px; color: #10b981; margin-top: 12px; padding: 8px; background: rgba(16, 185, 129, 0.1); border-radius: 6px; border-left: 3px solid #10b981;"><strong>🎯 预测命令:</strong> <code style="background: rgba(0,0,0,0.3); padding: 2px 6px; border-radius: 3px;">' + predictedCommand + '</code></div>';
                            }

                            const messageContent = streamingDiv.querySelector('.message-content');
                            if (messageContent) {
                                messageContent.insertAdjacentHTML('beforeend', commandHtml + statsHtml);
                            }
                            streamingDiv.classList.remove('streaming');
                            streamingDiv.removeAttribute('id');
                            
                            // 添加完成标识（带耗时）
                            const elapsedSecondsV3 = processingStartTime ? ((Date.now() - processingStartTime) / 1000).toFixed(2) : '0.00';
                            const completeDiv = document.createElement('div');
                            completeDiv.className = 'response-complete';
                            completeDiv.innerHTML = '<i class="fas fa-check-circle"></i><span>V3认知完成</span><span class="elapsed-time">⏱️ 耗时: ' + elapsedSecondsV3 + 's</span>';
                            streamingDiv.querySelector('.message-content').appendChild(completeDiv);
                            
                            // 添加操作按钮
                            addMessageActions(streamingDiv, fullResponse, 'assistant');
                            
                            // 保存到历史
                            messageHistory.push({ 
                                role: 'assistant', 
                                content: fullResponse, 
                                snn_mode: 'V3', 
                                command: predictedCommand,
                                v3_stats: v3Stats,
                                elapsed_ms: data.elapsed_ms
                            });
                            saveChatToHistory();
                        } else if (data.type === 'error') {
                            streamingDiv.innerHTML = '<div class="avatar"><i class="fas fa-robot"></i></div><div class="message-content" style="margin-left: 10px; margin-right: 120px;">❌ 错误: ' + data.message + '</div>';
                            streamingDiv.classList.remove('streaming');
                        }
                        
                        scrollToBottom();
                    } catch (e) {
                        // JSON解析错误，忽略
                    }
                }
            }
        }
        
    } catch (error) {
        console.error('[Nanobot V3 流式失败]', error);
        streamingDiv.innerHTML = `
            <div class="avatar"><i class="fas fa-robot"></i></div>
            <div class="message-content">❌ Nanobot V3 流式连接失败: ${error.message}</div>
        `;
        streamingDiv.classList.remove('streaming');
        messageHistory.push({ role: 'assistant', content: '❌ Nanobot V3 流式连接失败: ' + error.message });
        saveChatToHistory();
    } finally {
        isProcessing = false;
        updateSendButton();
    }
}

// NeuraCore神经核心: 300万神经元SNN意识主体
async function sendViaNeuraCore() {
    const input = document.getElementById('messageInput');
    const message = input.value.trim();
    
    if (!message) {
        return;
    }
    
    // 添加用户消息到界面和历史
    addMessageToUI('user', message, false, false, []);
    messageHistory.push({ role: 'user', content: message });
    saveChatToHistory();
    input.value = '';
    
    // 创建流式消息容器
    const streamingMsgId = 'stream-neuracore-' + Date.now();
    const streamingDiv = addMessageToUI('assistant', '🧠 NeuraCore3M神经核心 300万神经元初始化中...', true, true, []);
    streamingDiv.id = streamingMsgId;
    
    isProcessing = true;
    processingStartTime = Date.now();  // 记录开始时间
    updateSendButton();
    
    let fullResponse = '';
    let neuracoreStats = null;
    
    try {
        // 获取当前会话的历史
        const savedMessages = localStorage.getItem(`chat_${currentChatId}`);
        const historyForAPI = savedMessages ? JSON.parse(savedMessages) : messageHistory;
        
        const response = await fetch(`${CONFIG.API_URL}/api/neuracore/stream`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: message,
                history: (historyForAPI || []).slice(-10),
                session_id: currentSession || currentChatId,
                spikegpt_mode: spikeGPTModeEnabled  // 启用SpikeGPT融合模式
            })
        });
        
        // 检查高危操作
        if (await handleDangerCheck(response, message, streamingDiv)) {
            return;
        }
        
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            
            const chunk = decoder.decode(value);
            const lines = chunk.split('\n');
            
            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    const dataStr = line.slice(6);
                    if (dataStr === '[DONE]') {
                        continue;
                    }
                    
                    try {
                        const data = JSON.parse(dataStr);
                        
                        if (data.type === 'start') {
                            renderStreamingStatusFrame(streamingDiv, 'start', data, {
                                icon: 'fa-brain',
                                messageStyle: 'margin-left: 10px; margin-right: 120px;',
                                statusPrefix: '🧠 ',
                                fallbackStatusText: 'NeuraCore3M神经核心: 300万神经元构建中...',
                                markdownContent: ''
                            });
                        } else if (data.type === 'build') {
                            // 构建阶段进度
                            renderStreamingStatusFrame(streamingDiv, 'build', data, {
                                icon: 'fa-brain',
                                messageStyle: 'margin-left: 10px; margin-right: 120px;',
                                statusPrefix: '🏗️ ',
                                markdownContent: fullResponse || ''
                            });
                        } else if (data.type === 'cognition') {
                            // 认知阶段
                            neuracoreStats = data.stats;
                            renderStreamingStatusFrame(streamingDiv, 'cognition', data, {
                                icon: 'fa-brain',
                                messageStyle: 'margin-left: 10px; margin-right: 120px;',
                                statusPrefix: '⚡ ',
                                markdownContent: fullResponse || ''
                            });
                        } else if (data.type === 'chunk') {
                            // 增量内容
                            transitionStreamingLifecycle(streamingDiv, 'chunk');
                            fullResponse += data.content || '';
                            const contentDiv = streamingDiv.querySelector('.streaming-content');
                            const shouldStickToBottom = shouldAutoScroll();
                            if (contentDiv) {
                                renderStreamingMarkdownImmediate(contentDiv, fullResponse, streamingDiv);
                            }
                            if (shouldStickToBottom) scrollToBottom();
                        } else if (data.type === 'complete') {
                            // 完成
                            transitionStreamingLifecycle(streamingDiv, 'complete');
                            neuracoreStats = data.stats;
                            fullResponse = data.response;

                            renderStreamingFrame(streamingDiv, {
                                icon: 'fa-brain',
                                messageStyle: 'margin-left: 10px; margin-right: 120px;',
                                markdownContent: fullResponse
                            });
                            
                            // 构建统计HTML
                            const statsHtml = '<div class="snn-stats" style="font-size: 14px; color: #8b5cf6; margin-top: 16px; padding: 10px 14px; background: rgba(139, 92, 246, 0.1); border-radius: 6px; display: flex; flex-wrap: wrap; gap: 10px; border-left: 4px solid #8b5cf6;"><span style="font-weight: 600; width: 100%; margin-bottom: 6px; font-size: 15px;">📊 NeuraCore技术附录 | SNN意识核心</span><span>🧠6.0M神经元</span><span>⚡' + (neuracoreStats?.total_spikes || 0).toLocaleString() + '脉冲</span><span>⏱️' + (data.elapsed_ms || 0).toFixed(0) + 'ms</span></div>';

                            const messageContent = streamingDiv.querySelector('.message-content');
                            if (messageContent) {
                                messageContent.insertAdjacentHTML('beforeend', statsHtml);
                            }
                            streamingDiv.classList.remove('streaming');
                            streamingDiv.removeAttribute('id');
                            
                            // 添加完成标识（带耗时）
                            const elapsedSecondsNC = processingStartTime ? ((Date.now() - processingStartTime) / 1000).toFixed(2) : '0.00';
                            const completeDiv = document.createElement('div');
                            completeDiv.className = 'response-complete';
                            completeDiv.innerHTML = '<i class="fas fa-check-circle"></i><span>NeuraCore认知完成</span><span class="elapsed-time">⏱️ 耗时: ' + elapsedSecondsNC + 's</span>';
                            streamingDiv.querySelector('.message-content').appendChild(completeDiv);
                            
                            // 添加操作按钮
                            addMessageActions(streamingDiv, fullResponse, 'assistant');
                            
                            // 保存到历史
                            messageHistory.push({ 
                                role: 'assistant', 
                                content: fullResponse, 
                                neuracore_mode: true,
                                neuracore_stats: neuracoreStats,
                                elapsed_ms: data.elapsed_ms
                            });
                            saveChatToHistory();
                        } else if (data.type === 'error') {
                            streamingDiv.innerHTML = '<div class="avatar"><i class="fas fa-brain"></i></div><div class="message-content" style="margin-left: 10px; margin-right: 120px;">❌ NeuraCore错误: ' + data.message + '</div>';
                            streamingDiv.classList.remove('streaming');
                            messageHistory.push({ role: 'assistant', content: '❌ NeuraCore错误: ' + data.message });
                            saveChatToHistory();
                        }
                        
                        scrollToBottom();
                    } catch (e) {
                        // JSON解析错误，忽略
                    }
                }
            }
        }
        
    } catch (error) {
        console.error('[NeuraCore流式失败]', error);
        streamingDiv.innerHTML = `
            <div class="avatar"><i class="fas fa-brain"></i></div>
            <div class="message-content">❌ NeuraCore3M神经核心连接失败: ${error.message}</div>
        `;
        streamingDiv.classList.remove('streaming');
        messageHistory.push({ role: 'assistant', content: '❌ NeuraCore3M神经核心连接失败: ' + error.message });
        saveChatToHistory();
    } finally {
        isProcessing = false;
        updateSendButton();
    }
}

// 显示通知
function showNotification(message, type = 'info') {
    const colors = {
        success: '#10b981',
        warning: '#f59e0b',
        error: '#ef4444',
        info: '#3b82f6'
    };
    
    const notification = document.createElement('div');
    notification.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        padding: 12px 20px;
        background: ${colors[type] || colors.info};
        color: white;
        border-radius: 8px;
        z-index: 10000;
        animation: fadeIn 0.3s;
    `;
    notification.textContent = message;
    document.body.appendChild(notification);
    
    setTimeout(() => {
        notification.remove();
    }, 3000);
}

function showBackgroundTaskNotification(data = {}) {
    const title = String(data.title || data.task_preview || data.task_name || data.task_id || '后台任务').trim();
    const status = String(data.status || (data.success === false ? 'failed' : 'completed')).trim().toLowerCase();
    const statusLabel = status === 'failed' ? '失败' : '完成';
    const taskId = String(data.task_id || '').trim();
    const suffix = taskId ? ` · ${taskId}` : '';
    showToast(`🤖 ${escapeHtml(title)} 已${statusLabel}${suffix}`, status === 'failed' ? 'error' : 'success');
}

function buildResumeBaseState(messageContent, messageDiv = null) {
    if (!messageContent) {
        return { html: '', text: '' };
    }
    const clone = messageContent.cloneNode(true);
    clone.querySelectorAll('.message-bottom-toolbar, .response-telemetry, .quick-reply-bar, .change-set-host, .approval-pending-anchor').forEach(el => el.remove());
    
    // FIX: Use saved original markdown if available, instead of textContent which loses formatting
    let originalMarkdown = messageDiv?._fullResponseMarkdown || '';
    
    // FALLBACK: If _fullResponseMarkdown is empty, try to recover from localStorage streaming state
    if (!originalMarkdown && messageDiv?.id) {
        try {
            const chatId = messageDiv.closest('[data-chat-id]')?.dataset?.chatId || currentChatId;
            const streamingStateRaw = localStorage.getItem(`streaming_${chatId}`);
            if (streamingStateRaw) {
                const state = JSON.parse(streamingStateRaw);
                if (state?.content) {
                    originalMarkdown = state.content;
                }
            }
        } catch (e) {
            // ignore localStorage errors
        }
    }
    
    const fallbackText = (clone.textContent || '').trim();
    
    return {
        html: clone.innerHTML,
        text: originalMarkdown || fallbackText  // Prefer original markdown over textContent
    };
}

// 通过 API 发送消息（流式响应）
async function sendViaAPI(message, attachments = [], options = {}) {
    const savedMessages = localStorage.getItem(`chat_${currentChatId}`);
    const historyForSession = savedMessages ? JSON.parse(savedMessages) : messageHistory;
    const appendToMessage = options.appendToMessage || null;
    const hiddenContinuation = Boolean(options.hiddenContinuation);
    if (!hiddenContinuation) {
        clearChangeSetAutoAction();
    }
    const payload = {
        message: message,
        session_id: currentSession || currentChatId,
        history: (historyForSession || []).slice(-CONFIG.MAX_HISTORY),
        attachments: attachments,
        model: selectedModel,  // 传递选择的模型
        backend: selectedBackend,  // 传递调用方式：ollama 或 ollm
        mode: window._nanobotMode || 'code'  // Code/Ask/Plan mode
    };
    // P15-fix: Pass danger_approved flag to bypass danger check on re-send
    if (options.danger_approved) {
        payload.danger_approved = true;
    }
    
    // 调试日志 - 确认发送的model和backend参数
    console.log('════════════════════════════════════════');
    console.log('[sendViaAPI] 发送请求到后端:');
    console.log('  API端点:', `${CONFIG.API_URL}/api/chat/stream`);
    console.log('  payload.model:', payload.model);
    console.log('  payload.backend:', payload.backend);
    console.log('  selectedModel变量:', selectedModel);
    console.log('  selectedBackend变量:', selectedBackend);
    console.log('════════════════════════════════════════');
    
    try {
        // 创建用于显示流式响应的消息元素
        const messagesDiv = document.getElementById('messages');
        const messageDiv = appendToMessage || document.createElement('div');
        if (appendToMessage) {
            const previousStreaming = document.getElementById('streamingMessage');
            if (previousStreaming && previousStreaming !== messageDiv) {
                previousStreaming.removeAttribute('id');
                previousStreaming.classList.remove('streaming');
            }
            messageDiv.classList.add('streaming');
            messageDiv.id = 'streamingMessage';
            const messageContent = messageDiv.querySelector('.message-content');
            if (messageContent) {
                const resumeBase = buildResumeBaseState(messageContent, messageDiv);
                messageDiv._resumeBaseHtml = resumeBase.html;
                messageDiv._resumeBaseText = resumeBase.text;
                messageContent.querySelectorAll('.message-bottom-toolbar, .response-telemetry, .quick-reply-bar').forEach(el => el.remove());
                let streamingContent = messageContent.querySelector('.streaming-content');
                if (!streamingContent) {
                    streamingContent = document.createElement('div');
                    streamingContent.className = 'streaming-content';
                    streamingContent.style.whiteSpace = 'pre-wrap';
                    streamingContent.style.wordWrap = 'break-word';
                    messageContent.appendChild(streamingContent);
                }
                messageDiv._resumeSeedText = getStreamingContentFallbackText(streamingContent);
                // Note: _fullResponseMarkdown will be saved during streaming (line ~4407)
                // when the first chunk arrives, ensuring we have the complete markdown content
            }
        } else {
            messageDiv.className = 'message bot streaming';
            messageDiv.id = 'streamingMessage';
            messageDiv.innerHTML = `
                <div class="avatar">
                    <i class="fas fa-robot"></i>
                </div>
                <div class="message-content">
                    <div class="streaming-content" style="white-space: pre-wrap; word-wrap: break-word;"></div>
                </div>
            `;
            messagesDiv.appendChild(messageDiv);
        }

        scrollToBottom();
        
        const streamingContent = messageDiv.querySelector('.streaming-content');
        // FIX: Use original markdown content (_fullResponseMarkdown) if available
        // to preserve markdown formatting (**, `, >, etc.)
        let seedText = '';
        if (appendToMessage && messageDiv) {
            // Primary: use saved markdown content
            seedText = messageDiv._fullResponseMarkdown || '';
            
            // FALLBACK: If _fullResponseMarkdown is empty, try localStorage streaming state
            if (!seedText) {
                try {
                    const chatId = messageDiv.closest('[data-chat-id]')?.dataset?.chatId || currentChatId;
                    const streamingStateRaw = localStorage.getItem(`streaming_${chatId}`);
                    if (streamingStateRaw) {
                        const state = JSON.parse(streamingStateRaw);
                        if (state?.content) {
                            seedText = state.content;
                        }
                    }
                } catch (e) {
                    // ignore localStorage errors
                }
            }
            
            // Last resort: use textContent-based seed
            if (!seedText) {
                seedText = messageDiv._resumeSeedText || '';
            }
        }
        let fullResponse = seedText;
        let finalAnswerData = null;
        let thinkingTrace = '';
        let finalFooterHtml = '';
        let isStreaming = true;
        
        // 记录开始时的对话ID，用于后续检查
        const chatIdAtStart = currentChatId;
        activeStreamingChatId = chatIdAtStart;
        stopRequested = false;
        currentAbortController = new AbortController();
        processingStartTime = Date.now();

        // 立即保存初始流式状态
        localStorage.setItem(`streaming_${chatIdAtStart}`, JSON.stringify({
            isStreaming: true,
            content: '',
            timestamp: Date.now(),
            stopStreamingState: false
        }));
        
        // 保存流式状态到 localStorage 的函数 - 直接保存DOM内容确保完整
        function saveStreamingState() {
            if (isStreaming) {
                // 使用原始fullResponse保存，避免textContent破坏表格格式
                const currentContent = finalAnswerData?.content || fullResponse;
                localStorage.setItem(`streaming_${chatIdAtStart}`, JSON.stringify({
                    isStreaming: true,
                    content: currentContent,
                    timestamp: Date.now(),
                    final_answer: finalAnswerData,
                    thinking_trace: thinkingTrace
                }));
                
                // 同时保存当前消息历史（用户消息 + 部分AI回复）
                // 这样刷新后至少能看到用户提问
                const savedMessages = localStorage.getItem(`chat_${chatIdAtStart}`);
                let chatHistory = savedMessages ? JSON.parse(savedMessages) : [];
                
                // 确保用户消息只记录一次（避免流式保存时重复）
                // 检查此消息是否已存在于历史中的任何位置（不只是最后一条）
                const msgAlreadyExists = hiddenContinuation || chatHistory.some(
                    m => m.role === 'user' && m.content === message
                );
                if (!hiddenContinuation && !msgAlreadyExists) {
                    chatHistory.push({ role: 'user', content: message, attachments });
                } else if (!hiddenContinuation && attachments && attachments.length > 0) {
                    // 补充附件到已有条目
                    const existing = chatHistory.find(m => m.role === 'user' && m.content === message);
                    if (existing && !existing.attachments?.length) existing.attachments = attachments;
                }
                
                // 如果已经有AI回复在记录中，更新它；否则添加一个临时的
                const lastMsg = chatHistory[chatHistory.length - 1];
                if (lastMsg && lastMsg.role === 'assistant' && lastMsg.streaming) {
                    // 更新现有的流式AI回复
                    lastMsg.content = currentContent;
                    lastMsg.final_answer = finalAnswerData;
                    lastMsg.thinking_trace = thinkingTrace;
                } else if (!lastMsg || lastMsg.role !== 'assistant') {
                    // 添加临时的AI回复（正在生成中）
                    chatHistory.push({ role: 'assistant', content: currentContent, streaming: true, final_answer: finalAnswerData, thinking_trace: thinkingTrace });
                }
                
                chatHistory = normalizeChatHistory(chatHistory);
                localStorage.setItem(`chat_${chatIdAtStart}`, JSON.stringify(chatHistory));
                
                // 同时更新对话列表
                const history = JSON.parse(localStorage.getItem('nanobot_chats') || '[]');
                const chatIndex = history.findIndex(h => h.id === chatIdAtStart);
                if (chatIndex >= 0) {
                    history[chatIndex].preview = currentContent.substring(0, 50) + '...';
                    localStorage.setItem('nanobot_chats', JSON.stringify(history));
                }
            }
        }
        
        // 定期保存流式状态（每500ms）
        const streamingInterval = setInterval(saveStreamingState, 500);
        
        // 使用 fetch 调用流式 API
        const response = await fetch(`${CONFIG.API_URL}/api/chat/stream`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(payload),
            signal: currentAbortController.signal
        });
        cacheCurrentTaskIdFromResponse(response, 'sendViaAPI');
        
        // 检查是否是高危操作确认响应（非流式JSON响应）
        const contentType = response.headers.get('content-type') || '';
        console.log('[sendViaAPI] Response content-type:', contentType);
        console.log('[sendViaAPI] Response status:', response.status);
        
        if (contentType.includes('application/json') && !contentType.includes('text/event-stream')) {
            const jsonData = await response.json();
            console.log('[sendViaAPI] JSON响应数据:', jsonData);
            
            if (jsonData.danger_check_required) {
                console.log('[sendViaAPI] 检测到高危操作，显示确认弹窗');
                messageDiv.remove();
                
                // P15-fix: Loop breaker — if this was already an approved re-send, don't show modal again
                if (options.danger_approved) {
                    console.error('[sendViaAPI] danger_approved was set but backend still blocked! Server may need restart.');
                    hideTypingIndicator();
                    isProcessing = false;
                    updateSendButton();
                    addMessageToUI('bot', '⚠️ 服务器未识别 danger_approved 标志，请重启后端服务后重试。');
                    return;
                }
                
                // 确保 DangerModal 存在
                if (typeof DangerModal !== 'undefined' && DangerModal.showApprovalRequest) {
                    console.log('[sendViaAPI] 调用 DangerModal.showApprovalRequest');
                    DangerModal.showApprovalRequest({
                        request_id: 'danger_' + Date.now(),
                        danger_level: jsonData.danger_match.level,
                        category: jsonData.danger_match.category,
                        description: jsonData.danger_match.description,
                        suggestion: jsonData.danger_match.suggestion,
                        command: message,
                        timeout: 60
                    });
                } else {
                    console.error('[sendViaAPI] DangerModal 未定义!');
                    alert(`高危操作警告: ${jsonData.danger_match.description}\n\n建议: ${jsonData.danger_match.suggestion}`);
                }
                
                hideTypingIndicator();
                isProcessing = false;
                updateSendButton();
                return;
            }
            
            throw new Error(jsonData.message || '未知错误');
        }
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        // 获取 reader 读取流
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let sseBuffer = ''; // SSE 行缓冲：防止大 payload 跨 chunk 被截断
        let responseStats = null;
        
        let streamAborted = false;
        while (true) {
            let result;
            try {
                result = await reader.read();
            } catch (streamError) {
                console.warn('[sendViaAPI] Stream read error:', streamError);
                streamAborted = true;
                break;
            }
            const { done, value } = result;
            if (done) {
                // 处理缓冲区中可能残留的最后一行
                if (sseBuffer.trim().startsWith('data: ')) {
                    try {
                        const data = JSON.parse(sseBuffer.trim().slice(6));
                        if (data.type === 'done') {
                            console.log('[sendViaAPI] Done event from final buffer, stats:', data.stats ? 'present' : 'missing');
                            transitionStreamingLifecycle(messageDiv, 'done');
                            if (!fullResponse && data.response) {
                                fullResponse = data.response;
                            }
                            responseStats = data.stats || responseStats;
                        }
                    } catch (e) {
                        console.warn('[sendViaAPI] Final buffer parse failed, length:', sseBuffer.length);
                    }
                }
                break;
            }
            
            sseBuffer += decoder.decode(value, { stream: true });
            const lines = sseBuffer.split('\n');
            sseBuffer = lines.pop() || ''; // 保留最后一个（可能不完整的）行
            
            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    try {
                        const data = JSON.parse(line.slice(6));
                        
                        if (data.type === 'generation_start') {
                            // R1: Save checkpoint — U12b/U12c may roll back to here
                            messageDiv._generationStartPos = fullResponse.length;
                        } else if (data.type === 'clear_generation') {
                            // R1: U12b/U12c rejected this text — roll back to checkpoint
                            if (typeof messageDiv._generationStartPos === 'number') {
                                fullResponse = fullResponse.substring(0, messageDiv._generationStartPos);
                                renderStreamingMarkdownImmediate(streamingContent, fullResponse, messageDiv);
                            }
                        } else if (data.type === 'final_answer') {
                            finalAnswerData = {
                                content: data.content || '',
                                summary: data.summary || '',
                                details: data.details || '',
                                brief_first: Boolean(data.brief_first),
                                turn: data.turn || 0
                            };
                            const generationStartPos = typeof messageDiv._generationStartPos === 'number'
                                ? messageDiv._generationStartPos
                                : 0;
                            thinkingTrace = fullResponse.substring(0, generationStartPos).trim();
                            messageDiv._finalAnswerData = finalAnswerData;
                            messageDiv._thinkingTrace = thinkingTrace;
                            renderAssistantStructuredFrame(messageDiv, finalAnswerData, thinkingTrace, finalFooterHtml);
                            if (shouldAutoScroll()) scrollToBottom();
                        } else if (data.type === 'start') {
                            // 开始处理，显示状态
                            transitionStreamingLifecycle(messageDiv, 'start');
                            streamingContent.textContent = '🤔 思考中...';
                        } else if (data.type === 'chunk') {
                            // 收到内容块，实时显示
                            transitionStreamingLifecycle(messageDiv, 'chunk');
                            if (fullResponse === '' && data.content.includes('思考')) {
                                streamingContent.textContent = ''; // 清除"思考中"提示
                            }
                            fullResponse += data.content || '';
                            // FIX: Save original markdown content for resume after approval
                            messageDiv._fullResponseMarkdown = fullResponse;
                            const shouldStickToBottom = shouldAutoScroll();
                            renderStreamingMarkdownImmediate(streamingContent, fullResponse, messageDiv);
                            if (shouldStickToBottom) scrollToBottom();
                        } else if (data.type === 'heartbeat') {
                            // 收到心跳，更新状态提示（保留 markdown 渲染）
                            transitionStreamingLifecycle(messageDiv, 'heartbeat');
                            const heartbeatContent = buildHeartbeatContent(fullResponse, data);
                            renderStreamingMarkdownImmediate(streamingContent, heartbeatContent, messageDiv);
                            // 保存状态以便页面恢复时能看到最新时间
                            saveStreamingState();
                        } else if (data.type === 'done') {
                            // 处理完成
                            console.log('[sendViaAPI] Done event received, stats:', JSON.stringify(data.stats ? {model: data.stats.model_display, tokens: data.stats.completion_tokens, ms: data.stats.elapsed_ms} : null));
                            transitionStreamingLifecycle(messageDiv, 'done');
                            // Mark approval pending so addMessageActions shows "⏳ 等待审批" instead of "回答已完成"
                            if (data.approval_pending) {
                                messageDiv.dataset.approvalPending = 'true';
                                setMessagePendingChangeSetIds(messageDiv, data.pending_change_set_ids || []);
                                console.log('[sendViaAPI] Approval pending — change sets:', data.pending_change_set_ids);
                            }
                            if (!finalAnswerData && data.final_answer) {
                                finalAnswerData = data.final_answer;
                                if (typeof messageDiv._generationStartPos === 'number') {
                                    thinkingTrace = fullResponse.substring(0, messageDiv._generationStartPos).trim();
                                } else if (finalAnswerData.content && fullResponse.includes(finalAnswerData.content)) {
                                    // Fallback: extract everything before the final answer
                                    const faIdx = fullResponse.lastIndexOf(finalAnswerData.content);
                                    thinkingTrace = fullResponse.substring(0, faIdx).trim();
                                } else {
                                    // Last resort: use fullResponse as thinkingTrace
                                    thinkingTrace = fullResponse.trim();
                                }
                            }
                            // 保留前端已积累的完整内容（含工具执行记录），
                            // 仅当前端没有任何内容时才使用后端的 response 兜底
                            if (!fullResponse && data.response) {
                                fullResponse = data.response;
                            }
                            if (typeof fullResponse === 'string' && fullResponse) {
                                messageDiv._agenticTranscriptMarkdown = fullResponse;
                            }
                            // FIX: Ensure _fullResponseMarkdown is always saved with the final content
                            // so approval resume can access the complete markdown-formatted content
                            messageDiv._fullResponseMarkdown = fullResponse;
                            responseStats = data.stats || responseStats;
                            // P1-1: Update token status bar
                            if (data.stats) updateTokenStatusBar(data.stats);
                            // D2: Append verification/criteria badge if skill mode was active
                            const ds = data.stats || {};
                            if (ds.skill_mode) {
                                const vRan = ds.verification_ran;
                                const nudges = ds.criteria_nudges || 0;
                                const vClass = vRan ? 'd2-verify-pass' : 'd2-verify-skip';
                                const vIcon = vRan ? '🛡️' : '⚠️';
                                const vText = vRan ? '已验证' : '未验证';
                                let badge = `\n\n<span class="d2-verify-badge ${vClass}">${vIcon} ${vText}</span>`;
                                if (nudges > 0) {
                                    badge += `<span class="d2-verify-badge d2-verify-skip">📋 ${nudges} 条标准提示</span>`;
                                }
                                let footerButtons = '';
                                const nextMode = ds.suggested_next_mode;
                                if (nextMode) {
                                    const nextLabels = {refactor:'→ 切换到重构模式',verify:'→ 验证变更',debug:'→ 切换到调试模式'};
                                    const nextLabel = nextLabels[nextMode] || `→ /${nextMode}`;
                                    footerButtons = `\n<button class="d6-mode-switch-btn" onclick="document.getElementById('userInput').value='/${nextMode} ';document.getElementById('userInput').focus();">${nextLabel}</button>\n`;
                                }
                                if (finalAnswerData) {
                                    finalFooterHtml = `<div class="assistant-final-footer">${badge}${footerButtons}</div>`;
                                    renderAssistantStructuredFrame(messageDiv, finalAnswerData, thinkingTrace, finalFooterHtml);
                                } else {
                                    fullResponse += badge + '\n' + footerButtons;
                                    renderStreamingMarkdownImmediate(streamingContent, fullResponse, messageDiv);
                                }
                                syncPendingChangeSetsForSession(currentSession || currentChatId, messageDiv);
                            }
                        } else if (data.type === 'error') {
                            transitionStreamingLifecycle(messageDiv, 'error');
                            throw new Error(data.message);
                        } else if (data.type === 'error_warning') {
                            // 显示权限错误或执行警告
                            fullResponse += data.message;
                            if (streamingContent) {
                                renderStreamingMarkdownImmediate(streamingContent, fullResponse, messageDiv);
                            }
                            // 权限错误特殊标记
                            if (data.is_permission) {
                                console.warn('[PermissionError]', data.message);
                            }
                        } else if (data.type === 'agentic_turn') {
                            // Agentic loop: 新的 LLM 轮次开始
                            const turnNum = data.turn || 0;
                            console.log(`[AgenticLoop] Turn ${turnNum} starting`);
                            // 仅第 2 轮及以后显示分隔线（第 1 轮是初始请求，不需要标记）
                            if (turnNum > 1) {
                                fullResponse += `\n\n---\n`;
                            }
                            const shouldStickToBottom = shouldAutoScroll();
                            renderStreamingMarkdownImmediate(streamingContent, fullResponse, messageDiv);
                            if (shouldStickToBottom) scrollToBottom();
                        } else if (data.type === 'tool_start') {
                            // Agentic loop: 工具开始执行
                            const toolName = data.name || 'unknown';
                            const toolArgs = data.arguments || {};
                            console.log(`[AgenticLoop] Tool start: ${toolName}`, toolArgs);
                            let argsPreview = '';
                            try {
                                const argStr = JSON.stringify(toolArgs, null, 0);
                                argsPreview = argStr.length > 120 ? argStr.substring(0, 120) + '...' : argStr;
                            } catch (e) { argsPreview = '...'; }
                            fullResponse += `\n\n> 🔧 **${toolName}** \`${argsPreview}\`  \n> ⏳ 执行中...\n`;
                            const shouldStickToBottom = shouldAutoScroll();
                            renderStreamingMarkdownImmediate(streamingContent, fullResponse, messageDiv);
                            if (shouldStickToBottom) scrollToBottom();
                        } else if (data.type === 'tool_result') {
                            // Agentic loop: 工具执行完成
                            const toolName = data.name || 'unknown';
                            const success = data.success;
                            const elapsedMs = data.elapsed_ms || 0;
                            const output = data.output || '';
                            const changeSet = normalizeIncomingChangeSet(data.change_set || null, toolName);
                            console.log(`[AgenticLoop] Tool result: ${toolName} ${success ? 'OK' : 'FAIL'} (${elapsedMs}ms)`);
                            // file_read/grep_search/file_edit return detailed output — show more lines
                            const isDetailedTool = toolName === 'file_read' || toolName === 'grep_search' || toolName === 'file_edit';
                            const maxChars = isDetailedTool ? 2000 : 500;
                            const maxLines = isDetailedTool ? 30 : 10;
                            const outputPreview = output.length > maxChars ? output.substring(0, maxChars) + `\n... (${output.split('\n').length} lines total)` : output;
                            // Remove the "executing..." placeholder for this tool
                            const escapedName = toolName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
                            const pendingPattern = new RegExp(`\\n\\n> 🔧 \\*\\*${escapedName}\\*\\*[^\\n]*\\n> ⏳ 执行中\\.\\.\\.\\n`, 'g');
                            fullResponse = fullResponse.replace(pendingPattern, '\n');
                            // P1-3: Use styled card instead of blockquote
                            if (changeSet) {
                                fullResponse += `\n> ✅ **${toolName}** — 已生成待审批修改卡片。\n`;
                            } else {
                                const isDiff = toolName === 'file_edit' || toolName === 'file_write';
                                const cardOutput = outputPreview.split('\n').slice(0, maxLines).join('\n');
                                fullResponse += '\n\n' + buildToolResultCardHtml(toolName, success, elapsedMs, cardOutput, isDiff) + '\n\n';
                            }
                            const shouldStickToBottom = shouldAutoScroll();
                            renderStreamingMarkdownImmediate(streamingContent, fullResponse, messageDiv);
                            if (changeSet) {
                                renderChangeSetCard(changeSet, messageDiv);
                                // Auto-accept/reject if session-level auto-action is set
                                if (getChangeSetAutoAction() === 'accept') {
                                    console.log('[change-set] Auto-accepting (session policy):', changeSet.id);
                                    acceptChangeSet(changeSet.id, null);
                                } else if (getChangeSetAutoAction() === 'reject') {
                                    console.log('[change-set] Auto-rejecting (session policy):', changeSet.id);
                                    rejectChangeSet(changeSet.id, null);
                                }
                            } else if (toolName === 'file_edit' || toolName === 'file_write' || toolName === 'change_set_accept' || toolName === 'change_set_reject') {
                                syncPendingChangeSetsForSession(currentSession || currentChatId, messageDiv);
                            }
                            if (shouldStickToBottom) scrollToBottom();
                        } else if (data.type === 'todo_update') {
                            // P62: Todo list progress update
                            const todos = data.todos || [];
                            if (todos.length > 0) {
                                const completed = todos.filter(t => t.status === 'completed').length;
                                const total = todos.length;
                                const pct = Math.round((completed / total) * 100);
                                let todoMd = `\n\n> 📋 **任务进度** (${completed}/${total} — ${pct}%)\n`;
                                for (const t of todos) {
                                    const icon = t.status === 'completed' ? '✅' : t.status === 'in_progress' ? '🔄' : '⬜';
                                    todoMd += `> ${icon} ${t.content}\n`;
                                }
                                // Replace previous todo block or append
                                const todoPattern = /\n\n> 📋 \*\*任务进度\*\*[^]*?(?=\n\n[^>]|\n*$)/;
                                if (todoPattern.test(fullResponse)) {
                                    fullResponse = fullResponse.replace(todoPattern, todoMd);
                                } else {
                                    fullResponse += todoMd;
                                }
                                const shouldStickToBottom = shouldAutoScroll();
                                renderStreamingMarkdownImmediate(streamingContent, fullResponse, messageDiv);
                                if (shouldStickToBottom) scrollToBottom();
                            }
                        } else if (data.type === 'task_update') {
                            // Task status bar update
                            applyTaskUpdateEvent(data, currentSession || currentChatId);
                        } else if (data.type === 'skill_mode') {
                            // D2: Skill mode indicator badge
                            const mode = data.mode || '';
                            const modeLabels = {analyze:'分析模式',debug:'调试模式',verify:'验证模式',refactor:'重构模式'};
                            const modeIcons = {analyze:'🔍',debug:'🐛',verify:'✅',refactor:'♻️'};
                            const label = modeLabels[mode] || `/${mode}`;
                            const icon = modeIcons[mode] || '⚙️';
                            const colorClass = `d2-skill-${['analyze','debug','verify','refactor'].includes(mode) ? mode : 'default'}`;
                            const badge = `<div class="d2-skill-badge ${colorClass}"><span class="d2-badge-dot"></span>${icon} ${label}</div>\n\n`;
                            // D2 fix: Always place badge at the top of the response
                            if (fullResponse.trim().length > 0) {
                                fullResponse = badge + fullResponse;
                            } else {
                                fullResponse += badge;
                            }
                            console.log(`[D2] Skill mode: /${mode}`);
                            const shouldStickToBottom = shouldAutoScroll();
                            renderStreamingMarkdownImmediate(streamingContent, fullResponse, messageDiv);
                            if (shouldStickToBottom) scrollToBottom();
                        } else if (data.type === 'sub_agent_start') {
                            // D2: Sub-agent running banner
                            const saType = data.agent_type || 'general';
                            const saLabels = {explore:'探索 Agent',verify:'验证 Agent',plan:'规划 Agent',research:'研究 Agent',edit:'编辑 Agent'};
                            const saLabel = saLabels[saType] || `${saType} Agent`;
                            const taskPreview = data.task_preview ? `: ${data.task_preview}` : '';
                            fullResponse += `\n\n<div class="d2-sub-agent-banner" id="d2-sa-${saType}-${data.turn||0}"><div class="d2-sa-spinner"></div><span>🤖 <b>${saLabel}</b> 正在分析${taskPreview}</span></div>\n`;
                            console.log(`[D2] Sub-agent [${saType}] started`);
                            const shouldStickToBottom2 = shouldAutoScroll();
                            renderStreamingMarkdownImmediate(streamingContent, fullResponse, messageDiv);
                            if (shouldStickToBottom2) scrollToBottom();
                        } else if (data.type === 'sub_agent_end') {
                            // D2: Sub-agent completion — update banner
                            const saType = data.agent_type || 'general';
                            const ok = data.success;
                            const elapsed = data.elapsed ? `${data.elapsed.toFixed(1)}s` : '';
                            const bannerId = `d2-sa-${saType}-${data.turn||0}`;
                            // Replace the running banner with a completion banner
                            const runningPattern = new RegExp(`<div class="d2-sub-agent-banner" id="${bannerId}">[^]*?</div>`);
                            const doneClass = ok ? 'd2-sa-done' : 'd2-sa-fail';
                            const doneIcon = ok ? '✅' : '❌';
                            const doneLabel = ok ? '完成' : '失败';
                            const saLabels2 = {explore:'探索 Agent',verify:'验证 Agent',plan:'规划 Agent',research:'研究 Agent',edit:'编辑 Agent'};
                            const saLabel2 = saLabels2[saType] || `${saType} Agent`;
                            const replacement = `<div class="d2-sub-agent-banner ${doneClass}">${doneIcon} <b>${saLabel2}</b> ${doneLabel} ${elapsed ? `(${elapsed})` : ''}</div>`;
                            if (runningPattern.test(fullResponse)) {
                                fullResponse = fullResponse.replace(runningPattern, replacement);
                            } else {
                                fullResponse += `\n${replacement}\n`;
                            }
                            console.log(`[D2] Sub-agent [${saType}] ended: ${ok ? 'OK' : 'FAIL'} ${elapsed}`);
                            const shouldStickToBottom3 = shouldAutoScroll();
                            renderStreamingMarkdownImmediate(streamingContent, fullResponse, messageDiv);
                            if (shouldStickToBottom3) scrollToBottom();
                        } else if (data.type === 'background_task_done') {
                            showBackgroundTaskNotification(data);
                        } else if (data.type === 'agentic_max_turns') {
                            // Agentic loop: 达到最大轮次
                            console.warn(`[AgenticLoop] Max turns reached: ${data.turns}`);
                            fullResponse += `\n\n---\n⚠️ *已达最大工具调用轮次 (${data.turns})*\n`;
                            renderStreamingMarkdownImmediate(streamingContent, fullResponse, messageDiv);
                        } else if (data.type === 'tool_execution') {
                            // Legacy: 旧的工具执行通知（兼容）
                            console.log('[ToolExecution] Legacy tool execution:', data.tools);
                        }
                    } catch (e) {
                        console.warn('[sendViaAPI] SSE parse error, line length:', line.length, 'starts with:', line.substring(0, 30));
                    }
                }
            }
        }

        if (streamAborted) {
            // 使用原始fullResponse，避免textContent破坏表格格式
            const currentContent = fullResponse || (streamingContent ? streamingContent.textContent : '');
            if (stopRequested) {
                const stoppedContent = `${currentContent}\n\n---\n⏹ 已停止生成`;
                if (streamingContent) {
                    renderStreamingMarkdownImmediate(streamingContent, stoppedContent, messageDiv);
                }
                clearInterval(streamingInterval);
                isStreaming = false;
                localStorage.removeItem(`streaming_${chatIdAtStart}`);
                await finalizeStoppedResponse(chatIdAtStart, message, stoppedContent, attachments);
                return;
            }
            if (streamingContent) {
                renderStreamingMarkdownImmediate(
                    streamingContent,
                    `${currentContent}\n\n---\n🔄 [连接中断，等待后台结果同步...]`,
                    messageDiv
                );
            }
            saveStreamingState();
            startStreamingPoll(chatIdAtStart);
            return;
        }
        
        // 流式传输完成，清理定时器并清除状态
        clearInterval(streamingInterval);
        isStreaming = false;
        localStorage.removeItem(`streaming_${chatIdAtStart}`);
        
        // 使用原始完整响应保存（避免textContent破坏表格格式）
        // fullResponse 保留原始换行，而 streamingContent.textContent 可能会破坏格式
        const generatedFinalContent = finalAnswerData?.content || fullResponse || getStreamingContentFallbackText(streamingContent);
        const resumeBaseText = appendToMessage ? (messageDiv._resumeBaseText || '') : '';
        const resumeSeedText = appendToMessage ? (messageDiv._resumeSeedText || '') : '';
        const shouldPrefixResumeBase = Boolean(resumeBaseText)
            && !generatedFinalContent.startsWith(resumeBaseText)
            && (!resumeSeedText || !generatedFinalContent.startsWith(resumeSeedText));
        const finalContent = shouldPrefixResumeBase ? `${resumeBaseText}\n\n${generatedFinalContent}` : generatedFinalContent;
        const approvalPendingAtCompletion = messageDiv.dataset.approvalPending === 'true' || hasPendingChangeSets(messageDiv._changeSets || []);
        const preserveApprovalStreamFrame = approvalPendingAtCompletion && !finalAnswerData;
        
        // 更新UI显示（使用 markdown 渲染）
        if (finalAnswerData) {
            renderAssistantStructuredFrame(messageDiv, finalAnswerData, thinkingTrace, finalFooterHtml);
        } else if (streamingContent && !preserveApprovalStreamFrame) {
            renderStreamingMarkdownImmediate(streamingContent, finalContent, messageDiv);
        } else if (preserveApprovalStreamFrame) {
            console.log('[sendViaAPI] Preserving existing streaming DOM for pending approval completion');
        }
        
        // 如果 done 事件完全丢失，用客户端计时兜底
        if (!responseStats && processingStartTime) {
            const clientElapsedMs = Date.now() - processingStartTime;
            console.warn('[sendViaAPI] responseStats is null after stream ended. Synthesizing fallback from client timing:', clientElapsedMs, 'ms');
            responseStats = {
                model_display: selectedModel || '',
                model: selectedModel || '',
                phase: 'Reading / Generation',
                completion_tokens: 0,
                prompt_tokens: 0,
                total_tokens: 0,
                elapsed_ms: clientElapsedMs,
                tokens_per_second: 0,
                context_length: 0,
                output_limit: 0,
            };
        }
        console.log('[sendViaAPI] Final responseStats:', responseStats ? JSON.stringify({model: responseStats.model_display, tokens: responseStats.completion_tokens, ms: responseStats.elapsed_ms, tps: responseStats.tokens_per_second}) : 'NULL');

        const responseElapsedMs = responseStats && Number.isFinite(Number(responseStats.elapsed_ms))
            ? Number(responseStats.elapsed_ms)
            : (processingStartTime ? (Date.now() - processingStartTime) : null);

        // 更新历史记录（无论当前是否在查看这个对话）
        const savedMessages = localStorage.getItem(`chat_${chatIdAtStart}`);
        let chatHistory = savedMessages ? JSON.parse(savedMessages) : [];
        
        // 确保用户消息在历史记录中（避免重复插入）
        const userMsgExists = hiddenContinuation || chatHistory.some(
            m => m.role === 'user' && m.content === message
        );
        if (!hiddenContinuation && !userMsgExists) {
            chatHistory.push({ role: 'user', content: message, attachments });
        } else if (!hiddenContinuation && attachments && attachments.length > 0) {
            const existing = chatHistory.find(m => m.role === 'user' && m.content === message);
            if (existing && !existing.attachments?.length) existing.attachments = attachments;
        }
        
        // 添加/更新AI回复到历史（避免重复插入）
        const lastHistoryMsg = chatHistory[chatHistory.length - 1];
        if (lastHistoryMsg && lastHistoryMsg.role === 'assistant') {
            lastHistoryMsg.content = finalContent;
            lastHistoryMsg.streaming = false;
            lastHistoryMsg.final_answer = finalAnswerData;
            lastHistoryMsg.thinking_trace = thinkingTrace;
            if (typeof fullResponse === 'string' && fullResponse) {
                lastHistoryMsg.agentic_transcript = fullResponse;
            }
            if (Number.isFinite(responseElapsedMs) && responseElapsedMs >= 0) {
                lastHistoryMsg.elapsed_ms = responseElapsedMs;
            }
            if (responseStats) {
                lastHistoryMsg.llama_stats = responseStats;
            }
            // Preserve change_sets for F5 refresh
            if (messageDiv._changeSets && messageDiv._changeSets.length > 0) {
                lastHistoryMsg.change_sets = messageDiv._changeSets.map(item => ({ ...item }));
            }
            if (approvalPendingAtCompletion) {
                lastHistoryMsg.approval_pending = true;
            }
            if (Array.isArray(lastHistoryMsg.change_sets) && lastHistoryMsg.change_sets.length > 0) {
                lastHistoryMsg.pending_change_set_ids = lastHistoryMsg.change_sets
                    .map((changeSet) => changeSet?.id)
                    .filter((id) => typeof id === 'string' && id.trim())
            }
        } else {
            chatHistory.push({
                role: 'assistant',
                content: finalContent,
                streaming: false,
                final_answer: finalAnswerData,
                thinking_trace: thinkingTrace,
                ...(typeof fullResponse === 'string' && fullResponse ? { agentic_transcript: fullResponse } : {}),
                ...(Number.isFinite(responseElapsedMs) && responseElapsedMs >= 0 ? { elapsed_ms: responseElapsedMs } : {}),
                ...(responseStats ? { llama_stats: responseStats } : {}),
                ...(approvalPendingAtCompletion ? { approval_pending: true } : {}),
                ...(messageDiv._changeSets && messageDiv._changeSets.length > 0
                    ? {
                        pending_change_set_ids: messageDiv._changeSets
                            .map((changeSet) => changeSet?.id)
                            .filter((id) => typeof id === 'string' && id.trim())
                    }
                    : {}),
                ...(messageDiv._changeSets && messageDiv._changeSets.length > 0
                    ? { change_sets: messageDiv._changeSets.map(item => ({ ...item })) }
                    : {})
            });
        }
        localStorage.setItem(`chat_${chatIdAtStart}`, JSON.stringify(chatHistory));
        
        // 更新对话标题和预览
        const history = JSON.parse(localStorage.getItem('nanobot_chats') || '[]');
        const chatIndex = history.findIndex(h => h.id === chatIdAtStart);
        if (chatIndex >= 0) {
            history[chatIndex].preview = finalContent.substring(0, 50) + '...';
            localStorage.setItem('nanobot_chats', JSON.stringify(history));
        }
        
        // 只有当当前对话是发送时的对话时才更新UI
        if (currentChatId === chatIdAtStart) {
            // 更新全局messageHistory以便loadChat正确显示
            messageHistory = normalizeChatHistory(chatHistory);
            console.log('[sendViaAPI] Stream completed, messageHistory length:', messageHistory.length);
            
            // 流式完成后重新渲染最后一条消息以显示按钮
            const streamingMessage = document.getElementById('streamingMessage');
            if (streamingMessage) {
                // 移除流式标记，转换为普通消息
                streamingMessage.classList.remove('streaming');
                streamingMessage.removeAttribute('id');

                const lastAssistantIndex = messageHistory.findLastIndex(msg => msg.role === 'assistant');
                const lastMsg = lastAssistantIndex >= 0 ? messageHistory[lastAssistantIndex] : null;
                const lastMsgDiv = streamingMessage;

                // 清除旧的 bottomToolbar / telemetry / actions 并重新添加
                const oldToolbar = lastMsgDiv.querySelector('.message-bottom-toolbar');
                if (oldToolbar) oldToolbar.remove();
                const oldTelemetry = lastMsgDiv.querySelector('.response-telemetry');
                if (oldTelemetry) oldTelemetry.remove();
                const oldActions = lastMsgDiv.querySelector('.message-actions');
                if (oldActions) oldActions.remove();

                if (Number.isFinite(responseElapsedMs) && responseElapsedMs >= 0) {
                    lastMsgDiv.dataset.elapsedMs = String(responseElapsedMs);
                }
                if (responseStats) {
                    if (responseStats.model_display) {
                        lastMsgDiv.dataset.modelDisplayName = String(responseStats.model_display);
                    }
                    if (responseStats.model) lastMsgDiv.dataset.modelName = String(responseStats.model);
                    if (responseStats.phase) lastMsgDiv.dataset.llamaPhase = String(responseStats.phase);
                    if (Number.isFinite(Number(responseStats.prompt_tokens))) {
                        lastMsgDiv.dataset.promptTokens = String(Number(responseStats.prompt_tokens));
                    }
                    if (Number.isFinite(Number(responseStats.completion_tokens))) {
                        lastMsgDiv.dataset.completionTokens = String(Number(responseStats.completion_tokens));
                    }
                    if (Number.isFinite(Number(responseStats.total_tokens))) {
                        lastMsgDiv.dataset.totalTokens = String(Number(responseStats.total_tokens));
                    }
                    if (Number.isFinite(Number(responseStats.context_length))) {
                        lastMsgDiv.dataset.contextLength = String(Number(responseStats.context_length));
                    }
                    if (Number.isFinite(Number(responseStats.output_limit))) {
                        lastMsgDiv.dataset.outputLimit = String(Number(responseStats.output_limit));
                    }
                    if (Number.isFinite(Number(responseStats.elapsed_ms))) {
                        lastMsgDiv.dataset.llamaElapsedMs = String(Number(responseStats.elapsed_ms));
                    }
                    if (Number.isFinite(Number(responseStats.tokens_per_second))) {
                        lastMsgDiv.dataset.tokensPerSecond = String(Number(responseStats.tokens_per_second));
                    }
                }
                const finalChangeSets = (Array.isArray(lastMsgDiv._changeSets) && lastMsgDiv._changeSets.length > 0)
                    ? lastMsgDiv._changeSets
                    : (Array.isArray(lastMsg?.change_sets) ? lastMsg.change_sets : []);
                if (finalChangeSets.length > 0) {
                    lastMsgDiv._changeSets = finalChangeSets.map((item) => ({ ...item }));
                }
                if (hasPendingChangeSets(finalChangeSets) || isMessageApprovalPending(lastMsgDiv)) {
                    lastMsgDiv.dataset.approvalPending = 'true';
                } else {
                    delete lastMsgDiv.dataset.approvalPending;
                }
                
                // 重新添加消息操作按钮（包含完成标识和系统状态按钮）
                addMessageActions(lastMsgDiv, lastMsg?.content || finalContent, 'assistant', true);
                refreshApprovalCompletionState(lastMsgDiv, { scroll: isMessageApprovalPending(lastMsgDiv) });
                // Quick-reply buttons for confirmation questions
                renderQuickReplies(lastMsgDiv, lastMsg?.content || finalContent);
                if (finalChangeSets.length > 0) {
                    rerenderStoredChangeSetCards(lastMsgDiv);
                }
                if (isMessageApprovalPending(lastMsgDiv) && finalChangeSets.length === 0) {
                    ensureApprovalPendingAnchor(lastMsgDiv, { force: true });
                    syncPendingChangeSetsForSession(currentSession || currentChatId, lastMsgDiv);
                }
                if (finalChangeSets.length > 0 || isMessageApprovalPending(lastMsgDiv)) {
                    requestAnimationFrame(() => {
                        stabilizeChangeSetHost(lastMsgDiv, {
                            preferDefaultPosition: !lastMsgDiv.querySelector('.change-set-origin-anchor')
                        });
                    });
                }
                console.log('[sendViaAPI] Re-added message actions with completion buttons');
            }
            
            // 更新按钮状态
            updateSendButton();
            processingStartTime = null;
        } else {
            console.log('[sendViaAPI] Chat switched, not updating UI. currentChatId:', currentChatId, 'chatIdAtStart:', chatIdAtStart);
            processingStartTime = null;
        }
        
    } catch (error) {
        if (stopRequested || error?.name === 'AbortError') {
            const streamingContent = document.querySelector('#streamingMessage .streaming-content');
            // 使用fullResponse保持原始格式，避免textContent破坏表格
            const currentContent = fullResponse || (streamingContent ? streamingContent.textContent : '');
            const stoppedContent = currentContent ? `${currentContent}\n\n---\n⏹ 已停止生成` : '⏹ 已停止生成';
            if (streamingContent) {
                streamingContent.innerHTML = processMarkdown(stoppedContent);
            }
            await finalizeStoppedResponse(currentChatId, message, stoppedContent, attachments);
            processingStartTime = null;
            return;
        }
        console.error('API call failed:', error);
        
        // 如果流式消息已创建，更新它显示错误
        const streamingMessage = document.getElementById('streamingMessage');
        if (streamingMessage) {
            const contentDiv = streamingMessage.querySelector('.message-content');
            contentDiv.innerHTML = `❌ 错误: ${error.message}`;
            streamingMessage.classList.remove('streaming');
            streamingMessage.removeAttribute('id');
        } else {
            addMessageToUI('bot', `❌ 发送失败: ${error.message}`);
        }
        processingStartTime = null;
    }
}

// UI 函数

// 解析思考过程 - 简化版本，不干扰正常内容格式
// DISABLED: 原始版本会破坏代码块格式
function parseThinking(content) {
    // 直接返回原文，不做任何处理
    // 后端返回的格式已经是正确的，不需要额外解析
    return {
        text: content,
        thinkingBlocks: []
    };
}

// 确保表格前后有空行，使 marked 能正确识别
function ensureTableSpacing(text) {
    const lines = text.split('\n');
    const result = [];
    
    // 辅助函数：检查是否是表格行
    const isTableLine = (line) => {
        const trimmed = line.trim();
        return trimmed.startsWith('|') && trimmed.endsWith('|');
    };
    
    // 辅助函数：检查是否是分隔符行
    const isSeparatorLine = (line) => {
        const trimmed = line.trim();
        // 匹配 |------|------| 或 | --- | --- | 格式的分隔符行
        return /^\|[-:\s|]+\|$/.test(trimmed) && trimmed.includes('|');
    };
    
    // 辅助函数：检查是否是 HTML 结束标签
    const isHTMLEndTag = (line) => {
        return /^\s*<\/[^>]+>\s*$/.test(line);
    };
    
    for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        const trimmed = line.trim();
        
        // 检查是否是 HTML 结束标签，且下一行是表格
        if (isHTMLEndTag(line) && i < lines.length - 1) {
            const nextLine = lines[i + 1];
            // 如果下一行是表格行（有缩进的表格），先添加空行
            if (nextLine.trim().startsWith('|') || nextLine.includes('\t')) {
                result.push(line);
                result.push('');  // 添加空行
                continue;
            }
        }
        
        if (isTableLine(line)) {
            // 检查这是表格的开始还是结束
            const prevLine = i > 0 ? lines[i - 1].trim() : '';
            const isTableStart = !isTableLine(lines[i - 1] || '');
            
            // 如果是表格开始，且前一行不是空行，添加空行
            if (isTableStart && prevLine !== '' && !isSeparatorLine(line)) {
                result.push('');
            }
            
            result.push(line);
            
            // 检查下一行是否还是表格
            const nextLine = i < lines.length - 1 ? lines[i + 1] : '';
            const isTableEnd = !isTableLine(nextLine);
            
            // 如果是表格结束，且下一行不是空行，添加空行
            if (isTableEnd && nextLine.trim() !== '' && nextLine.trim() !== '```') {
                result.push('');
            }
        } else {
            result.push(line);
        }
    }
    
    return result.join('\n');
}

function getOrCreateChangeSetHost(messageDiv) {
    const messageContent = messageDiv.querySelector(':scope > .message-content') || messageDiv;
    let host = messageContent.querySelector(':scope > .change-set-host') || messageDiv.querySelector(':scope > .change-set-host');
    if (host && host.parentElement !== messageContent) {
        messageContent.appendChild(host);
    }
    if (!host) {
        host = document.createElement('div');
        host.className = 'change-set-host';
        host.dataset.defaultWidth = '520px';
        host.dataset.defaultHeight = '';
        host.dataset.defaultLeft = 'auto';
        host.dataset.defaultTop = 'auto';
        host.dataset.defaultRight = '12px';
        host.dataset.defaultBottom = '12px';
        host.dataset.defaultTransform = 'none';
        host.dataset.defaultMaxWidth = '520px';
        host.dataset.defaultMaxHeight = '';
        host.style.position = 'absolute';
        host.style.zIndex = '99999';
        host.style.right = '12px';
        host.style.bottom = '12px';
        host.style.left = 'auto';
        host.style.top = 'auto';
        host.style.maxWidth = '520px';
        host.style.maxHeight = 'calc(100% - 24px)';
        host.style.background = 'rgba(15, 23, 42, 0.97)';
        host.style.borderRadius = '14px';
        host.style.boxShadow = '0 8px 32px rgba(0,0,0,0.5)';
        host.style.padding = '8px';
        host.style.display = 'flex';
        host.style.flexDirection = 'column';
        host.style.gap = '8px';
        host.tabIndex = -1;
        messageContent.style.position = 'relative';
        messageContent.style.overflow = 'visible';
        messageContent.appendChild(host);
        console.log('[change-set-diag] Created new floating .change-set-host');
    }
    let dragBar = host.querySelector(':scope > .change-set-drag-bar');
    if (!dragBar) {
        dragBar = document.createElement('div');
        dragBar.className = 'change-set-drag-bar';
        dragBar.style.cssText = 'cursor:grab;user-select:none;padding:6px 10px;margin:-8px -8px 0 -8px;background:rgba(148,163,184,0.15);border-radius:14px 14px 0 0;font-size:12px;color:rgba(226,232,240,0.88);display:flex;align-items:center;justify-content:space-between;gap:10px;';
        dragBar.innerHTML = '<span><i class="fas fa-grip-lines"></i> 审批窗口</span><span style="color:rgba(148,163,184,0.78);font-size:11px;">拖动移动</span>';
        host.prepend(dragBar);
    }
    let body = host.querySelector(':scope > .change-set-host-body');
    if (!body) {
        body = document.createElement('div');
        body.className = 'change-set-host-body';
        body.style.display = 'flex';
        body.style.flexDirection = 'column';
        body.style.gap = '8px';
        body.style.maxHeight = 'calc(70vh - 36px)';
        body.style.overflowY = 'auto';
        Array.from(host.children).forEach((child) => {
            if (child !== dragBar) {
                body.appendChild(child);
            }
        });
        host.appendChild(body);
    }
    host.style.display = 'flex';
    host.style.flexDirection = 'column';
    host.style.visibility = 'visible';
    host.style.opacity = '1';
    host.style.pointerEvents = 'auto';
    body.style.display = 'flex';
    body.style.flexDirection = 'column';
    body.style.gap = '8px';
    if (dragBar.dataset.dragBound !== 'true') {
        let isDragging = false, startX = 0, startY = 0, startLeft = 0, startTop = 0;
        dragBar.addEventListener('mousedown', (e) => {
            isDragging = true;
            activateShortcutPanel(host);
            host.dataset.userPositioned = 'true';
            dragBar.style.cursor = 'grabbing';
            const rect = host.getBoundingClientRect();
            const parentRect = (host.offsetParent || messageContent).getBoundingClientRect();
            startX = e.clientX;
            startY = e.clientY;
            startLeft = rect.left - parentRect.left + (host.offsetParent || messageContent).scrollLeft;
            startTop = rect.top - parentRect.top + (host.offsetParent || messageContent).scrollTop;
            host.style.left = startLeft + 'px';
            host.style.top = startTop + 'px';
            host.style.right = 'auto';
            host.style.bottom = 'auto';
            e.preventDefault();
        });
        dragBar.addEventListener('click', () => activateShortcutPanel(host));
        document.addEventListener('mousemove', (e) => {
            if (!isDragging) return;
            const dx = e.clientX - startX;
            const dy = e.clientY - startY;
            let newLeft = startLeft + dx;
            let newTop = startTop + dy;
            const parent = host.offsetParent || messageContent;
            const maxLeft = Math.max(0, parent.clientWidth - host.offsetWidth);
            const maxTop = Math.max(0, parent.scrollHeight - host.offsetHeight);
            newLeft = Math.max(0, Math.min(newLeft, maxLeft));
            newTop = Math.max(0, Math.min(newTop, maxTop));
            host.style.left = newLeft + 'px';
            host.style.top = newTop + 'px';
        });
        document.addEventListener('mouseup', () => {
            if (isDragging) {
                isDragging = false;
                dragBar.style.cursor = 'grab';
            }
        });
        dragBar.dataset.dragBound = 'true';
    }
    if (host.dataset.clickActivateBound !== 'true') {
        host.addEventListener('mousedown', (e) => {
            if (e.target.closest('pre, code, .change-set-diff-view')) return;
            activateShortcutPanel(host);
        });
        host.addEventListener('click', (e) => {
            if (e.target.closest('pre, code, .change-set-diff-view')) return;
            activateShortcutPanel(host);
        });
        host.dataset.clickActivateBound = 'true';
    }
    bindApprovalPanelContextMenu(host);
    return host;
}

function ensureTaskHistoryPanel() {
    ensureTaskStatusBarStyles();
    let modal = document.getElementById('taskHistoryModal');
    if (modal) return modal;

    modal = document.createElement('div');
    modal.id = 'taskHistoryModal';
    modal.className = 'task-history-modal';
    modal.innerHTML = `
        <div class="task-history-modal__panel" role="dialog" aria-modal="true" aria-labelledby="taskHistoryTitle">
            <div class="task-history-modal__header">
                <div class="task-history-modal__title">
                    <strong id="taskHistoryTitle">任务状态历史</strong>
                    <span id="taskHistorySubtitle">点击任务 ID 查看完整时间线</span>
                </div>
                <button class="task-history-modal__close" type="button" aria-label="关闭任务历史面板">×</button>
            </div>
            <div class="task-history-modal__body" id="taskHistoryBody">
                <div class="task-history-modal__empty">请选择一个任务 ID 查看状态历史。</div>
            </div>
            <div class="task-history-modal__footer" id="taskHistoryFooter"></div>
        </div>
    `;

    modal.addEventListener('click', (event) => {
        if (event.target === modal) {
            closeTaskHistoryPanel();
        }
    });
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && taskHistoryPanelOpen) {
            event.preventDefault();
            closeTaskHistoryPanel();
        }
    });
    const closeBtn = modal.querySelector('.task-history-modal__close');
    if (closeBtn) {
        closeBtn.addEventListener('click', closeTaskHistoryPanel);
    }
    document.body.appendChild(modal);
    renderTaskHistoryFooter(null);
    return modal;
}

function closeTaskHistoryPanel() {
    const modal = document.getElementById('taskHistoryModal');
    if (modal) {
        modal.classList.remove('active');
    }
    taskHistoryPanelOpen = false;
}

function openTaskHistoryFromCache(taskId) {
    if (!taskId) return;
    const modal = ensureTaskHistoryPanel();
    taskHistoryPanelOpen = true;
    taskHistoryPanelTaskId = taskId;
    const cached = taskStatusBarCache || loadTaskStatusBarCache(currentSession || currentChatId);
    const rootTask = cached?.root_task || cached?.task || {};
    const objective = rootTask.objective || rootTask.current_step || '';
    const subtitle = document.getElementById('taskHistorySubtitle');
    if (subtitle) {
        subtitle.textContent = objective
            ? `任务 ID: ${taskId} · ${objective}`
            : `任务 ID: ${taskId}`;
    }
    if (modal) {
        modal.classList.add('active');
    }
    renderTaskHistoryFooter(taskId);
    const stateHistory = Array.isArray(rootTask.state_history) ? rootTask.state_history : [];
    const childTasks = cached?.child_tasks || cached?.tasks || [];

    if (stateHistory.length > 0) {
        renderTaskHistoryEntries({
            state_history: stateHistory,
            task: rootTask,
            task_tree: null,
            child_count: childTasks.length,
        });
    } else {
        const body = document.getElementById('taskHistoryBody');
        if (body) {
            body.innerHTML = `
                <div class="task-history-modal__empty">
                    <div style="font-weight: 600; margin-bottom: 6px;">暂无历史记录</div>
                    <div>当前任务尚未发生状态迁移，后续变化会自动推送到此面板。</div>
                </div>
            `;
        }
    }
}

function openTaskHistoryPanel(taskId) {
    if (!taskId) return;
    ensureTaskHistoryPanel();
    taskHistoryPanelOpen = true;
    taskHistoryPanelTaskId = taskId;
    const modal = document.getElementById('taskHistoryModal');
    const subtitle = document.getElementById('taskHistorySubtitle');
    if (subtitle) {
        subtitle.textContent = `任务 ID: ${taskId}`;
    }
    if (modal) {
        modal.classList.add('active');
    }
    renderTaskHistoryFooter(taskId);
    renderTaskHistoryLoading(taskId);
    loadTaskHistoryForTask(taskId).catch((error) => {
        renderTaskHistoryError(error?.message || '加载任务历史失败');
    });
}

function renderTaskHistoryLoading(taskId) {
    const body = document.getElementById('taskHistoryBody');
    if (!body) return;
    body.innerHTML = `
        <div class="task-history-modal__loading">
            正在加载 <code>${escapeHtml(taskId)}</code> 的状态历史…
        </div>
    `;
}

function renderTaskHistoryError(message) {
    const body = document.getElementById('taskHistoryBody');
    if (!body) return;
    body.innerHTML = `
        <div class="task-history-modal__error">
            ${escapeHtml(message)}
        </div>
    `;
}

function getTaskStateOptions() {
    return [
        ['created', '待创建'],
        ['planned', '计划中'],
        ['in_progress', '执行中'],
        ['waiting_approval', '等待审批'],
        ['verifying', '验证中'],
        ['blocked', '阻塞'],
        ['completed', '已完成'],
        ['failed', '失败'],
        ['cancelled', '已取消'],
        ['backgrounded', '后台执行'],
    ];
}

function renderTaskHistoryFooter(taskId) {
    const footer = document.getElementById('taskHistoryFooter');
    if (!footer) return;
    const debugMode = Boolean(window.__NANOBOT_DEBUG_MODE__ || localStorage.getItem('nanobot_debug_mode') === '1');
    if (!debugMode) {
        footer.innerHTML = '';
        footer.style.display = 'none';
        return;
    }

    footer.style.display = 'block';
    const options = getTaskStateOptions().map(([value, label]) => `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`).join('');
    footer.innerHTML = `
        <div class="task-history-modal__manual-transition">
            <div class="task-history-modal__manual-title">手动迁移</div>
            <div class="task-history-modal__manual-form">
                <select class="task-history-modal__manual-state" id="taskHistoryManualState">
                    ${options}
                </select>
                <input class="task-history-modal__manual-reason" id="taskHistoryManualReason" type="text" placeholder="填写原因，例如 manual override" />
                <button class="task-history-modal__manual-submit" type="button" id="taskHistoryManualSubmit">提交</button>
            </div>
            <div class="task-history-modal__manual-hint">仅在调试模式下可见。</div>
        </div>
    `;

    const submitBtn = document.getElementById('taskHistoryManualSubmit');
    if (submitBtn) {
        submitBtn.onclick = async () => {
            const stateInput = document.getElementById('taskHistoryManualState');
            const reasonInput = document.getElementById('taskHistoryManualReason');
            const state = stateInput ? String(stateInput.value || '').trim() : '';
            const reason = reasonInput ? String(reasonInput.value || '').trim() : '';
            if (!taskId || !state) {
                showNotification('请选择要迁移到的状态', 'warning');
                return;
            }
            submitBtn.disabled = true;
            try {
                const response = await fetch(`${CONFIG.API_URL}/api/task/${encodeURIComponent(taskId)}/transition`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ state, reason: reason || 'manual override' }),
                });
                const payload = await response.json().catch(() => ({}));
                if (!response.ok || payload?.success === false) {
                    const message = payload?.detail || payload?.error || `HTTP ${response.status}`;
                    showNotification(`手动迁移失败：${message}`, 'error');
                    return;
                }
                showNotification(`手动迁移成功：${payload.state}`, 'success');
                await loadTaskHistoryForTask(taskId);
            } catch (error) {
                showNotification(`手动迁移失败：${error?.message || error}`, 'error');
            } finally {
                submitBtn.disabled = false;
            }
        };
    }
}

function renderTaskHistoryEntries(payload) {
    const body = document.getElementById('taskHistoryBody');
    if (!body) return;
    const history = Array.isArray(payload?.state_history) ? payload.state_history : [];
    const task = payload?.task || {};
    const taskTree = payload?.task_tree || null;
    const childCount = Number(payload?.child_count ?? (taskTree?.children?.length || 0));

    if (!history.length) {
        body.innerHTML = `
            <div class="task-history-modal__empty">
                <div style="font-weight: 600; margin-bottom: 6px;">暂无状态历史</div>
                <div>这个任务目前还没有发生过状态迁移。</div>
            </div>
        `;
        return;
    }

    const taskObjective = task.objective || task.current_step || '';
    const taskTitleText = task.title || task.id || '未命名任务';
    const summary = `任务标题：${taskTitleText}${taskObjective ? ' · 目标：' + taskObjective : ''} · 子任务 ${childCount} 个 · 历史 ${history.length} 条`;
    const entries = history.map((entry, index) => {
        const timestamp = entry.timestamp || entry.created_at || '';
        const fromState = entry.from_state || 'unknown';
        const toState = entry.to_state || 'unknown';
        const source = entry.source || 'unknown';
        const reason = entry.reason || '';
        const taskId = entry.task_id || task.id || taskHistoryPanelTaskId || '';
        const taskTitle = entry.task_title || task.title || '';
        const entryObjective = entry.objective || task.objective || '';
        const entryStep = entry.current_step || task.current_step || '';
        return `
            <div class="task-history-entry">
                <div class="task-history-entry__top">
                    <div class="task-history-entry__states">
                        <span>#${index + 1}</span>
                        <code>${escapeHtml(fromState)}</code>
                        <i class="fas fa-arrow-right"></i>
                        <code>${escapeHtml(toState)}</code>
                    </div>
                    <div class="task-history-entry__source">${escapeHtml(timestamp || 'no timestamp')}</div>
                </div>
                <div class="task-history-entry__meta">
                    <span><strong>task</strong> ${escapeHtml(taskId)}</span>
                    ${taskTitle ? `<span><strong>title</strong> ${escapeHtml(taskTitle)}</span>` : ''}
                    <span><strong>source</strong> ${escapeHtml(source)}</span>
                </div>
                ${entryObjective ? `<div class="task-history-entry__reason" style="margin-top:4px;"><strong style="color:#e2e8f0;">目标</strong> ${escapeHtml(entryObjective)}</div>` : ''}
                ${entryStep && entryStep !== entryObjective ? `<div class="task-history-entry__reason" style="margin-top:2px;"><strong style="color:#e2e8f0;">当前步骤</strong> ${escapeHtml(entryStep)}</div>` : ''}
                ${reason ? `<div class="task-history-entry__reason">${escapeHtml(reason)}</div>` : ''}
            </div>
        `;
    }).join('');

    body.innerHTML = `
        <div style="margin-bottom: 12px; color: #94a3b8; font-size: 12px; line-height: 1.5;">${escapeHtml(summary)}</div>
        <div class="task-history-log">${entries}</div>
    `;
}

async function loadTaskHistoryForTask(taskId) {
    const response = await fetch(`${CONFIG.API_URL}/api/task/${encodeURIComponent(taskId)}/debug`, {
        method: 'GET',
        headers: { 'Content-Type': 'application/json' },
    });
    if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
    }
    const payload = await response.json();
    const loadedTask = payload?.task || {};
    const loadedObjective = loadedTask.objective || loadedTask.current_step || '';
    if (loadedObjective) {
        const subtitle = document.getElementById('taskHistorySubtitle');
        if (subtitle) {
            subtitle.textContent = `任务 ID: ${taskId} · ${loadedObjective}`;
        }
    }
    renderTaskHistoryEntries(payload);
    return payload;
}

function onTaskIdClicked(taskId) {
    if (!taskId || taskId === '—') return;
    openTaskHistoryPanel(taskId);
}

function getTaskStateClass(state) {
    const normalized = String(state || '').trim().toLowerCase();
    if (normalized === 'in_progress') return 'task-state--in_progress';
    if (normalized === 'completed') return 'task-state--completed';
    if (normalized === 'failed') return 'task-state--failed';
    return 'task-state--other';
}

function ensureApprovalPendingAnchor(messageDiv, options = {}) {
    if (!messageDiv) return null;
    const messageContent = messageDiv.querySelector(':scope > .message-content') || messageDiv;
    if (!messageContent) return null;
    const { force = false } = options;
    const shouldShow = force || isMessageApprovalPending(messageDiv);
    let anchor = messageContent.querySelector(':scope > .approval-pending-anchor');
    if (!shouldShow) {
        if (anchor) anchor.remove();
        return null;
    }
    if (!anchor) {
        anchor = document.createElement('div');
        anchor.className = 'approval-pending-anchor';
        anchor.style.margin = '0 0 12px 0';
        anchor.style.padding = '8px 12px';
        anchor.style.border = '1px dashed rgba(251, 191, 36, 0.35)';
        anchor.style.borderRadius = '10px';
        anchor.style.background = 'rgba(251, 191, 36, 0.08)';
        anchor.style.color = '#fbbf24';
        anchor.style.fontSize = '12px';
        anchor.style.fontWeight = '600';
        anchor.innerHTML = '<i class="fas fa-hourglass-half"></i><span style="margin-left:6px;">已生成待审批修改卡片</span>';
    }
    const host = messageContent.querySelector(':scope > .change-set-host');
    if (anchor.parentElement !== messageContent) {
        messageContent.insertBefore(anchor, host || messageContent.firstChild);
    } else if (host && anchor.nextSibling !== host) {
        messageContent.insertBefore(anchor, host);
    }
    return anchor;
}

function positionChangeSetHostAtOrigin(messageDiv, host) {
    if (!messageDiv || !host) return;
    if (host.dataset.userPositioned === 'true') return;
    const messageContent = messageDiv.querySelector(':scope > .message-content') || messageDiv;
    const persistentApprovalAnchor = messageContent
        ? messageContent.querySelector(':scope > .approval-pending-anchor')
        : null;
    const anchors = Array.from(messageDiv.querySelectorAll('.change-set-origin-anchor'));
    let anchor = null;
    if (persistentApprovalAnchor && isMessageApprovalPending(messageDiv)) {
        anchor = persistentApprovalAnchor;
    } else if (anchors.length) {
        const index = Math.min(Math.max((host.querySelectorAll('[data-change-set-id]').length || 1) - 1, 0), anchors.length - 1);
        anchor = anchors[index];
    } else {
        anchor = ensureApprovalPendingAnchor(messageDiv);
    }
    if (!anchor) return;
    const line = anchor.closest('.change-set-origin-line') || anchor;
    const lineRect = line.getBoundingClientRect();
    const contentRect = messageContent.getBoundingClientRect();
    const hostWidth = host.offsetWidth || 520;
    const hostHeight = host.offsetHeight || 240;
    const gap = 12;
    let left = messageContent.clientWidth - hostWidth - gap;
    const lineRelativeRight = lineRect.right - contentRect.left + messageContent.scrollLeft;
    if (lineRelativeRight + gap + hostWidth <= messageContent.clientWidth - gap) {
        left = lineRelativeRight + gap;
    }
    let top = lineRect.top - contentRect.top + messageContent.scrollTop;
    const maxLeft = Math.max(0, messageContent.clientWidth - hostWidth);
    const maxTop = Math.max(0, messageContent.scrollHeight - hostHeight);
    left = Math.max(0, Math.min(left, maxLeft));
    top = Math.max(0, Math.min(top, maxTop));
    host.style.left = `${left}px`;
    host.style.top = `${top}px`;
    host.style.right = 'auto';
    host.style.bottom = 'auto';
}

function resetChangeSetHostToDefaultPosition(host) {
    if (!host) return;
    host.style.left = host.dataset.defaultLeft || 'auto';
    host.style.top = host.dataset.defaultTop || 'auto';
    host.style.right = host.dataset.defaultRight || '12px';
    host.style.bottom = host.dataset.defaultBottom || '12px';
    host.style.maxWidth = host.dataset.defaultMaxWidth || '520px';
    host.style.maxHeight = host.dataset.defaultMaxHeight || 'calc(100% - 24px)';
    if (host.dataset.defaultTransform) {
        host.style.transform = host.dataset.defaultTransform;
    }
}

function stabilizeChangeSetHost(messageDiv, options = {}) {
    if (!messageDiv) return;
    const { preferDefaultPosition = false } = options;
    const messageContent = messageDiv.querySelector(':scope > .message-content') || messageDiv;
    const host = getOrCreateChangeSetHost(messageDiv);
    if (!host || !messageContent) return;
    messageDiv.style.overflow = 'visible';
    messageContent.style.position = 'relative';
    messageContent.style.overflow = 'visible';
    host.style.display = 'flex';
    host.style.visibility = 'visible';
    host.style.opacity = '1';
    host.style.pointerEvents = 'auto';
    host.style.zIndex = '99999';
    const approvalAnchor = ensureApprovalPendingAnchor(messageDiv);
    const hasAnchors = messageDiv.querySelectorAll('.change-set-origin-anchor').length > 0 || Boolean(approvalAnchor);
    if (preferDefaultPosition && !hasAnchors) {
        resetChangeSetHostToDefaultPosition(host);
        return;
    }
    if (!hasAnchors) {
        resetChangeSetHostToDefaultPosition(host);
        return;
    }
    positionChangeSetHostAtOrigin(messageDiv, host);
}

function bindApprovalPanelContextMenu(panel) {
    if (!panel || panel.dataset.contextMenuBound === 'true') return;
    panel.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        e.stopPropagation();
        showPanelContextMenu(e.clientX, e.clientY, panel);
    });
    enablePanelShortcuts(panel, 'approval');
    panel.dataset.contextMenuBound = 'true';
}

function rememberChangeSetForMessage(messageDiv, changeSet) {
    if (!messageDiv || !changeSet?.id) return;
    const existing = Array.isArray(messageDiv._changeSets) ? messageDiv._changeSets.slice() : [];
    const index = existing.findIndex((item) => item?.id === changeSet.id);
    if (index >= 0) {
        existing[index] = { ...existing[index], ...changeSet };
    } else {
        existing.push(changeSet);
    }
    messageDiv._changeSets = existing;
    console.log('[change-set-diag] rememberChangeSetForMessage: stored', existing.length, 'changeSets, latest id=', changeSet.id);
}

function getChangeSetAutoActionSessionId() {
    return String(currentSession || currentChatId || '');
}

function setChangeSetAutoAction(action, sessionId = getChangeSetAutoActionSessionId()) {
    window._changeSetAutoAction = action || '';
    window._changeSetAutoActionSessionId = sessionId || '';
}

function clearChangeSetAutoAction() {
    window._changeSetAutoAction = '';
    window._changeSetAutoActionSessionId = '';
}

function getChangeSetAutoAction(sessionId = getChangeSetAutoActionSessionId()) {
    if (!window._changeSetAutoAction) return '';
    if (!window._changeSetAutoActionSessionId) return '';
    return window._changeSetAutoActionSessionId === String(sessionId || '')
        ? window._changeSetAutoAction
        : '';
}

function formatChangeSetResumeSummary(changeSet) {
    const changeSetId = String(changeSet?.id || '').trim();
    const changeSetType = String(changeSet?.type || '').trim();
    const headerParts = [changeSetId];
    if (changeSetType) {
        headerParts.push(`(${changeSetType})`);
    }

    const files = Array.isArray(changeSet?.files) ? changeSet.files : [];
    const fileLines = files.map((file) => {
        const path = String(file?.path || '').trim();
        const summary = String(file?.summary || '').trim();
        if (!path && !summary) return '';
        return summary ? `  - ${path}${path ? ' — ' : ''}${summary}` : `  - ${path}`;
    }).filter(Boolean);

    if (fileLines.length === 0) {
        return `- ${headerParts.join(' ')}`;
    }

    return [`- ${headerParts.join(' ')}`, ...fileLines].join('\n');
}

function parseChangeSetTimestamp(changeSet) {
    const raw = changeSet?.updated_at || changeSet?.created_at || changeSet?.updatedAt || changeSet?.timestamp || '';
    if (!raw) return null;
    const date = new Date(String(raw).replace('Z', '+00:00'));
    return Number.isNaN(date.getTime()) ? null : date;
}

function formatRelativeTimeLabel(changeSet) {
    const date = parseChangeSetTimestamp(changeSet);
    if (!date) return '刚刚';
    const diffMs = Date.now() - date.getTime();
    if (diffMs < 60_000) return '刚刚';
    const diffMinutes = Math.floor(diffMs / 60_000);
    if (diffMinutes < 60) return `${diffMinutes} 分钟前`;
    const diffHours = Math.floor(diffMinutes / 60);
    if (diffHours < 24) return `${diffHours} 小时前`;
    const diffDays = Math.floor(diffHours / 24);
    if (diffDays < 7) return `${diffDays} 天前`;
    return date.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' });
}

function getChangeSetDiffStats(changeSet) {
    const files = Array.isArray(changeSet?.files) ? changeSet.files : [];
    let linesAdded = 0;
    let linesRemoved = 0;
    files.forEach((file) => {
        const diffText = String(file?.diff || '');
        diffText.split('\n').forEach((line) => {
            if (line.startsWith('+++') || line.startsWith('---')) return;
            if (line.startsWith('@@')) return;
            if (line.startsWith('+')) linesAdded += 1;
            else if (line.startsWith('-')) linesRemoved += 1;
        });
    });
    return { linesAdded, linesRemoved };
}

function getChangeSetTypeLabel(changeSet) {
    if ((changeSet?.type || 'direct') === 'rollback_review') return '回滚审查';
    return '直接修改';
}

function collectPendingChangeSetsFromHistory(sessionId = currentSession || currentChatId) {
    if (!sessionId || sessionId !== (currentSession || currentChatId)) return [];
    const messages = Array.isArray(messageHistory) ? messageHistory : [];
    const pending = [];
    const seenIds = new Set();
    for (let i = 0; i < messages.length; i += 1) {
        const msg = messages[i];
        if (!msg || !Array.isArray(msg.change_sets) || msg.change_sets.length === 0) continue;
        const scopedIds = Array.isArray(msg.pending_change_set_ids) && msg.pending_change_set_ids.length > 0
            ? new Set(msg.pending_change_set_ids.filter((id) => typeof id === 'string' && id.trim()))
            : null;
        for (const changeSet of msg.change_sets) {
            if (!changeSet || !changeSet.id || seenIds.has(changeSet.id)) continue;
            const currentState = changeSetStateCache.get(changeSet.id);
            const status = String(currentState?.status || changeSet?.status || 'pending').toLowerCase();
            if (status && status !== 'pending') continue;
            if (scopedIds && !scopedIds.has(changeSet.id)) continue;
            seenIds.add(changeSet.id);
            pending.push({ ...changeSet });
        }
    }
    pending.sort((a, b) => {
        const aTime = parseChangeSetTimestamp(a)?.getTime() || 0;
        const bTime = parseChangeSetTimestamp(b)?.getTime() || 0;
        if (aTime !== bTime) return bTime - aTime;
        return String(a.id || '').localeCompare(String(b.id || ''));
    });
    return pending;
}

function ensureFileChangeSummaryCard() {
    const card = document.getElementById('fileChangeSummary');
    if (!card) return null;
    if (card.dataset.initialized === 'true') return card;
    const header = card.querySelector('#fileChangeSummaryHeader');
    if (header) {
        header.addEventListener('click', () => {
            if (fileChangeSummaryState.busy) return;
            fileChangeSummaryState.expanded = !fileChangeSummaryState.expanded;
            renderFileChangeSummaryCard(fileChangeSummaryState.changeSets, {
                loading: fileChangeSummaryState.loading,
                error: fileChangeSummaryState.error,
                expanded: fileChangeSummaryState.expanded,
            });
        });
    }
    card.dataset.initialized = 'true';
    return card;
}

function renderFileChangeSummaryCard(changeSets = [], options = {}) {
    const card = ensureFileChangeSummaryCard();
    if (!card) return;
    const normalizedChangeSets = cloneChangeSetList(changeSets).filter((changeSet) => {
        if (!changeSet || !changeSet.id) return false;
        const status = String(changeSetStateCache.get(changeSet.id)?.status || changeSet.status || 'pending').toLowerCase();
        return status === 'pending';
    });
    fileChangeSummaryState.changeSets = normalizedChangeSets;
    fileChangeSummaryState.loading = Boolean(options.loading);
    fileChangeSummaryState.busy = Boolean(options.busy);
    fileChangeSummaryState.error = String(options.error || '');
    if (typeof options.expanded === 'boolean') {
        fileChangeSummaryState.expanded = options.expanded;
    }
    fileChangeSummaryState.lastUpdatedAt = Date.now();

    const shouldShow = fileChangeSummaryState.loading || normalizedChangeSets.length > 0 || Boolean(fileChangeSummaryState.error);
    card.hidden = !shouldShow;
    card.dataset.expanded = shouldShow && fileChangeSummaryState.expanded ? 'true' : 'false';

    const titleEl = card.querySelector('#fileChangeSummaryTitle');
    const subtitleEl = card.querySelector('#fileChangeSummarySubtitle');
    const badgeEl = card.querySelector('#fileChangeSummaryBadge');
    const bodyEl = card.querySelector('#fileChangeSummaryBody');
    const headerBtn = card.querySelector('#fileChangeSummaryHeader');
    const pendingCount = normalizedChangeSets.length;
    const pendingFileCount = normalizedChangeSets.reduce((count, changeSet) => count + (Array.isArray(changeSet?.files) ? changeSet.files.length : 0), 0);
    const latestChangeSet = normalizedChangeSets[0] || null;
    const latestTimeLabel = latestChangeSet ? formatRelativeTimeLabel(latestChangeSet) : '';

    if (titleEl) {
        titleEl.textContent = fileChangeSummaryState.loading
            ? '正在加载文件代码变更…'
            : (pendingCount > 0 ? `${pendingCount} 个待审批修改` : '暂无待审批修改');
    }
    if (subtitleEl) {
        if (fileChangeSummaryState.error && pendingCount === 0) {
            subtitleEl.textContent = `加载失败：${fileChangeSummaryState.error}`;
        } else if (pendingCount > 0) {
            subtitleEl.textContent = `${pendingFileCount} 个文件 · 最近 ${latestTimeLabel}`;
        } else if (fileChangeSummaryState.loading) {
            subtitleEl.textContent = '正在刷新当前会话的待审批文件变更';
        } else {
            subtitleEl.textContent = '点击查看最近的文件变更';
        }
    }
    if (badgeEl) {
        badgeEl.textContent = String(pendingCount);
    }
    if (headerBtn) {
        headerBtn.disabled = fileChangeSummaryState.busy || fileChangeSummaryState.loading;
    }

    if (!bodyEl) return;
    if (!shouldShow) {
        bodyEl.innerHTML = '';
        return;
    }
    if (!fileChangeSummaryState.expanded) {
        bodyEl.innerHTML = '';
        return;
    }

    if (fileChangeSummaryState.loading && pendingCount === 0) {
        bodyEl.innerHTML = '<div class="file-change-summary__loading"><i class="fas fa-spinner fa-spin"></i> 正在加载文件代码变更…</div>';
        return;
    }
    if (fileChangeSummaryState.error && pendingCount === 0) {
        bodyEl.innerHTML = `<div class="file-change-summary__error">${escapeHtml(fileChangeSummaryState.error)}</div>`;
        return;
    }
    if (pendingCount === 0) {
        bodyEl.innerHTML = '<div class="file-change-summary__empty">当前没有待审批的文件变更。</div>';
        return;
    }

    const groupsHtml = normalizedChangeSets.map((changeSet) => {
        const files = Array.isArray(changeSet.files) ? changeSet.files : [];
        const statusMeta = getChangeSetStatusMeta(String(changeSetStateCache.get(changeSet.id)?.status || changeSet.status || 'pending').toLowerCase());
        const typeLabel = getChangeSetTypeLabel(changeSet);
        const timeLabel = formatRelativeTimeLabel(changeSet);
        const fileCountText = `${files.length} 个文件`;
        const fileHtml = files.length > 0
            ? files.map((file) => {
                const path = String(file?.path || '').trim();
                const summary = String(file?.summary || '').trim();
                const diffStats = getChangeSetDiffStats({ files: [file] });
                const fallbackSummary = file?.created
                    ? '新建文件'
                    : (file?.before_exists === false && file?.after_exists === true
                        ? '新增文件内容'
                        : (file?.after_exists === false ? '删除文件' : '修改文件'));
                const changeSummary = summary || fallbackSummary;
                const statsText = (diffStats.linesAdded || diffStats.linesRemoved)
                    ? `+${diffStats.linesAdded} / -${diffStats.linesRemoved} 行`
                    : '';
                return `
                    <div class="file-change-summary__file">
                        <div class="file-change-summary__file-path" title="${escapeHtml(path)}"><i class="fas fa-file-code"></i><span>${escapeHtml(path || '未命名文件')}</span></div>
                        <div class="file-change-summary__file-summary">${escapeHtml(changeSummary)}</div>
                        ${statsText ? `<div class="file-change-summary__file-stats">${escapeHtml(statsText)}</div>` : ''}
                    </div>
                `;
            }).join('')
            : '<div class="file-change-summary__empty">该修改尚未提供文件明细。</div>';

        return `
            <div class="file-change-summary__group" data-change-set-id="${escapeHtml(changeSet.id)}">
                <div class="file-change-summary__group-header">
                    <div class="file-change-summary__group-title" title="${escapeHtml(changeSet.id)}">${escapeHtml(changeSet.id)} · ${escapeHtml(typeLabel)}</div>
                    <div class="file-change-summary__group-time">${escapeHtml(timeLabel)}</div>
                </div>
                <div class="file-change-summary__group-meta">${escapeHtml(statusMeta.text)} · ${escapeHtml(fileCountText)}${changeSet.source ? ` · 来源 ${escapeHtml(changeSet.source)}` : ''}</div>
                <div class="file-change-summary__files">${fileHtml}</div>
            </div>
        `;
    }).join('');

    const actionsHtml = `
        <div class="file-change-summary__actions">
            <button type="button" class="file-change-summary__action file-change-summary__action--reject" id="fileChangeSummaryRejectAll" ${fileChangeSummaryState.busy ? 'disabled' : ''} title="拒绝全部待审批修改">
                <i class="fas fa-times"></i>
                <span>Reject all</span>
            </button>
            <button type="button" class="file-change-summary__action file-change-summary__action--accept" id="fileChangeSummaryAcceptAll" ${fileChangeSummaryState.busy ? 'disabled' : ''} title="接受全部待审批修改">
                <i class="fas fa-check"></i>
                <span>Accept all</span>
            </button>
        </div>
    `;

    bodyEl.innerHTML = `${groupsHtml}${actionsHtml}`;
    const rejectAllBtn = bodyEl.querySelector('#fileChangeSummaryRejectAll');
    const acceptAllBtn = bodyEl.querySelector('#fileChangeSummaryAcceptAll');
    if (rejectAllBtn) {
        rejectAllBtn.onclick = () => handleFileChangeSummaryBulkAction('reject');
    }
    if (acceptAllBtn) {
        acceptAllBtn.onclick = () => handleFileChangeSummaryBulkAction('accept');
    }
}

function refreshFileChangeSummaryFromCurrentState(sessionId = currentSession || currentChatId, options = {}) {
    const activeSessionId = sessionId || currentSession || currentChatId;
    const pendingChangeSets = collectPendingChangeSetsFromHistory(activeSessionId);
    fileChangeSummaryState.sessionId = activeSessionId || '';
    fileChangeSummaryState.loading = false;
    fileChangeSummaryState.error = '';
    renderFileChangeSummaryCard(pendingChangeSets, options);
    return pendingChangeSets;
}

async function syncFileChangeSummaryForActiveSession(sessionId = currentSession || currentChatId, options = {}) {
    const activeSessionId = sessionId || currentSession || currentChatId;
    if (!activeSessionId) {
        fileChangeSummaryState.sessionId = '';
        fileChangeSummaryState.changeSets = [];
        fileChangeSummaryState.loading = false;
        fileChangeSummaryState.error = '';
        renderFileChangeSummaryCard([], { expanded: false });
        return [];
    }
    fileChangeSummaryState.sessionId = activeSessionId;
    fileChangeSummaryState.loading = true;
    renderFileChangeSummaryCard(fileChangeSummaryState.changeSets, {
        loading: true,
        error: '',
        expanded: typeof options.expanded === 'boolean' ? options.expanded : fileChangeSummaryState.expanded,
    });
    try {
        const response = await fetch(`${CONFIG.API_URL}/api/changes/pending?session_id=${encodeURIComponent(activeSessionId)}`);
        const data = await response.json();
        const remoteChangeSets = response.ok && data.success && Array.isArray(data.change_sets)
            ? data.change_sets
            : [];
        const fallbackChangeSets = remoteChangeSets.length > 0 ? remoteChangeSets : collectPendingChangeSetsFromHistory(activeSessionId);
        fileChangeSummaryState.changeSets = cloneChangeSetList(fallbackChangeSets);
        fileChangeSummaryState.loading = false;
        fileChangeSummaryState.error = '';
        renderFileChangeSummaryCard(fileChangeSummaryState.changeSets, {
            expanded: typeof options.expanded === 'boolean' ? options.expanded : fileChangeSummaryState.expanded,
        });
        return fileChangeSummaryState.changeSets;
    } catch (error) {
        console.warn('[file-change-summary] sync failed:', error);
        const fallbackChangeSets = collectPendingChangeSetsFromHistory(activeSessionId);
        fileChangeSummaryState.changeSets = cloneChangeSetList(fallbackChangeSets);
        fileChangeSummaryState.loading = false;
        fileChangeSummaryState.error = error?.message || '加载文件代码变更失败';
        renderFileChangeSummaryCard(fileChangeSummaryState.changeSets, {
            error: fileChangeSummaryState.error,
            expanded: typeof options.expanded === 'boolean' ? options.expanded : fileChangeSummaryState.expanded,
        });
        return fileChangeSummaryState.changeSets;
    }
}

async function handleFileChangeSummaryBulkAction(action) {
    const pendingChangeSets = Array.isArray(fileChangeSummaryState.changeSets)
        ? fileChangeSummaryState.changeSets.filter((changeSet) => {
            const status = String(changeSetStateCache.get(changeSet.id)?.status || changeSet?.status || 'pending').toLowerCase();
            return status === 'pending';
        })
        : [];
    if (pendingChangeSets.length === 0 || fileChangeSummaryState.busy) return;
    fileChangeSummaryState.busy = true;
    renderFileChangeSummaryCard(pendingChangeSets, {
        loading: fileChangeSummaryState.loading,
        error: fileChangeSummaryState.error,
        busy: true,
        expanded: true,
    });
    try {
        const ordered = action === 'reject' ? pendingChangeSets.slice().reverse() : pendingChangeSets;
        for (const changeSet of ordered) {
            if (action === 'reject') {
                await rejectChangeSet(changeSet.id, null);
            } else {
                await acceptChangeSet(changeSet.id, null);
            }
        }
        showNotification(action === 'reject' ? '已拒绝全部待审批修改' : '已接受全部待审批修改', 'success');
    } catch (error) {
        console.error('[file-change-summary] bulk action failed:', error);
        showNotification(action === 'reject' ? '拒绝全部失败' : '接受全部失败', 'error');
    } finally {
        fileChangeSummaryState.busy = false;
        await refreshFileChangeSummaryFromCurrentState(fileChangeSummaryState.sessionId || currentSession || currentChatId, {
            expanded: true,
        });
    }
}

function buildApprovalResumeMessage(messageDiv, action) {
    const changeSets = Array.isArray(messageDiv?._changeSets) ? messageDiv._changeSets : [];
    const rejectedChangeSets = changeSets.filter((changeSet) => {
        const state = changeSet?.id ? changeSetStateCache.get(changeSet.id) : null;
        const status = String(state?.status || changeSet?.status || '').toLowerCase();
        return status === 'rejected' || status === 'reverted';
    });

    const rejectedSummary = rejectedChangeSets.length > 0
        ? rejectedChangeSets.map(formatChangeSetResumeSummary).join('\n')
        : '- 本次修改已被拒绝，但当前没有可展示的变更摘要。';

    if (action === 'accepted') {
        return '用户已接受本次待审批修改。请继续当前任务；如果当前任务已经完成，请直接给出最终完成结果，不要再次要求审批。';
    }

    const sessionAutoAction = getChangeSetAutoAction();
    const readOnlyLockNote = sessionAutoAction === 'reject'
        ? '\n本次会话已设置为“本次会话一直拒绝”，后续任何写入都会继续被拒绝。请转为只读分析、重新规划或给出替代方案，不要再生成任何 file_edit / file_write。'
        : '';

    return [
        '用户已拒绝本次待审批修改。以下修改已被明确否决，不能重复生成：',
        rejectedSummary,
        '请继续当前任务，但不要再次尝试相同修改或同一目标的补丁。先重新分析，给出不同的替代方案；如果还需要改文件，请先产出新的计划再执行。',
        readOnlyLockNote,
    ].filter(Boolean).join('\n');
}

function normalizeIncomingChangeSet(changeSet, toolName = '') {
    if (!changeSet || typeof changeSet !== 'object') return changeSet;
    const normalized = { ...changeSet };
    const sourceTool = String(toolName || '').toLowerCase();
    if ((sourceTool === 'file_edit' || sourceTool === 'file_write') && normalized.status !== 'pending') {
        normalized.status = 'pending';
    }
    return normalized;
}

function clearCurrentTaskIdCache(source = '') {
    cachedCurrentTaskId = '';
    taskStatusBarTreeTaskId = '';
    taskStatusBarTreeExpanded = false;
    try {
        localStorage.removeItem('nanobot_current_task_id');
    } catch (error) {
        console.warn('[clearCurrentTaskIdCache] Failed to clear task id cache:', source, error);
    }
}

function cacheCurrentTaskIdFromResponse(response, source = '') {
    if (!response || !response.headers) return '';
    const headerName = 'X-Nanobot-Current-Task-ID';
    const taskId = response.headers.get(headerName) || response.headers.get(headerName.toLowerCase()) || '';
    return cacheCurrentTaskId(taskId, source);
}

function mergeChangeSetsById(existingChangeSets = [], incomingChangeSets = []) {
    const order = [];
    const merged = new Map();
    const ingest = (changeSet) => {
        const normalized = normalizeIncomingChangeSet(changeSet, changeSet?.source || '');
        if (!normalized || !normalized.id) return;
        if (!merged.has(normalized.id)) {
            order.push(normalized.id);
        }
        merged.set(normalized.id, { ...(merged.get(normalized.id) || {}), ...normalized });
    };
    (Array.isArray(existingChangeSets) ? existingChangeSets : []).forEach(ingest);
    (Array.isArray(incomingChangeSets) ? incomingChangeSets : []).forEach(ingest);
    return order.map((id) => merged.get(id));
}

function hasPendingChangeSets(changeSets = []) {
    return Array.isArray(changeSets) && changeSets.some((changeSet) => {
        const state = getChangeSetState(changeSet);
        return (state.status || changeSet?.status || 'pending') === 'pending';
    });
}

function isMessageApprovalPending(messageDiv) {
    if (!messageDiv) return false;
    if (messageDiv.dataset.approvalPending === 'true') return true;
    if (hasPendingChangeSets(messageDiv._changeSets || [])) return true;
    const cards = Array.from(messageDiv.querySelectorAll('[data-change-set-id]'));
    return cards.some((card) => {
        const changeSetId = card.dataset.changeSetId;
        const state = changeSetId ? changeSetStateCache.get(changeSetId) : null;
        const status = state?.status || card.dataset.status || 'pending';
        return status === 'pending';
    });
}

function refreshApprovalCompletionState(messageDiv, options = {}) {
    if (!messageDiv) return;
    const { scroll = false } = options;
    const approvalPending = isMessageApprovalPending(messageDiv);
    const completeDiv = messageDiv.querySelector('.response-complete');
    if (completeDiv) {
        if (approvalPending) {
            completeDiv.innerHTML = '<i class="fas fa-hourglass-half"></i><span>请审批</span>';
        } else {
            const elapsedMsFromMeta = Number(messageDiv?.dataset?.elapsedMs);
            const hasMetaElapsed = Number.isFinite(elapsedMsFromMeta) && elapsedMsFromMeta >= 0;
            const hasLiveElapsed = !hasMetaElapsed && processingStartTime !== null && isProcessing;
            const elapsedMs = hasMetaElapsed
                ? elapsedMsFromMeta
                : (hasLiveElapsed ? (Date.now() - processingStartTime) : null);
            const elapsedSec = Number.isFinite(elapsedMs) && elapsedMs > 0 ? (elapsedMs / 1000).toFixed(2) : null;
            completeDiv.innerHTML = '<i class="fas fa-check-circle"></i><span>回答已完成</span>' + (elapsedSec !== null ? '<span class="elapsed-time">⏱️ 耗时: ' + elapsedSec + 's</span>' : '');
        }
    }
    if (scroll && approvalPending) {
        requestAnimationFrame(() => {
            scrollToBottom();
        });
    }
}

function persistLatestAssistantChangeSets(sessionId, changeSets = []) {
    const activeSessionId = sessionId || currentSession || currentChatId;
    const cloned = cloneChangeSetList(changeSets);
    if (!activeSessionId || cloned.length === 0) return;
    const scopedIds = cloned
        .map((changeSet) => changeSet?.id)
        .filter((id) => typeof id === 'string' && id.trim());
    try {
        const raw = localStorage.getItem(`chat_${activeSessionId}`);
        if (!raw) return;
        const chatHistory = JSON.parse(raw);
        if (!Array.isArray(chatHistory) || chatHistory.length === 0) return;
        for (let i = chatHistory.length - 1; i >= 0; i--) {
            if (chatHistory[i]?.role === 'assistant') {
                chatHistory[i].change_sets = cloneChangeSetList(cloned);
                chatHistory[i].pending_change_set_ids = scopedIds;
                localStorage.setItem(`chat_${activeSessionId}`, JSON.stringify(chatHistory));
                break;
            }
        }
        if (activeSessionId === currentChatId && Array.isArray(messageHistory)) {
            for (let i = messageHistory.length - 1; i >= 0; i--) {
                if (messageHistory[i]?.role === 'assistant') {
                    messageHistory[i].change_sets = cloneChangeSetList(cloned);
                    messageHistory[i].pending_change_set_ids = scopedIds;
                    break;
                }
            }
        }
    } catch (error) {
        console.warn('[change-set] Failed to persist hydrated change sets:', error);
    }
}

function needsPendingChangeSetHydration(messageDiv) {
    if (!messageDiv) return false;
    const stored = Array.isArray(messageDiv._changeSets) ? messageDiv._changeSets : [];
    if (stored.length === 0) {
        return messageDiv.dataset.approvalPending === 'true';
    }
    if (!(messageDiv.dataset.approvalPending === 'true' || hasPendingChangeSets(stored))) {
        return false;
    }
    const renderable = getRenderableChangeSetCount(stored);
    const pending = getPendingChangeSetCount(stored);
    return renderable === 0 || renderable < stored.length || (pending > 0 && renderable < pending);
}

function getMessageScopedChangeSetIds(messageDiv) {
    if (!messageDiv) return [];
    const explicitIds = Array.isArray(messageDiv._pendingChangeSetIds)
        ? messageDiv._pendingChangeSetIds.filter((id) => typeof id === 'string' && id.trim())
        : [];
    const storedPendingIds = Array.isArray(messageDiv._changeSets)
        ? messageDiv._changeSets
            .map((changeSet) => changeSet?.id)
            .filter((id) => typeof id === 'string' && id.trim())
        : [];
    if (storedPendingIds.length > 0) {
        return Array.from(new Set([...storedPendingIds, ...explicitIds]));
    }
    if (explicitIds.length > 0) {
        return Array.from(new Set(explicitIds));
    }
    const storedIds = Array.isArray(messageDiv._changeSets)
        ? messageDiv._changeSets
            .filter((changeSet) => String(changeSet?.status || '').toLowerCase() === 'pending')
            .map((changeSet) => changeSet?.id)
            .filter((id) => typeof id === 'string' && id.trim())
        : [];
    return Array.from(new Set(storedIds));
}

function setMessagePendingChangeSetIds(messageDiv, changeSetIds = []) {
    if (!messageDiv) return;
    const normalized = Array.from(new Set(
        (Array.isArray(changeSetIds) ? changeSetIds : [])
            .filter((id) => typeof id === 'string' && id.trim())
    ));
    if (normalized.length > 0) {
        messageDiv._pendingChangeSetIds = normalized;
    } else {
        delete messageDiv._pendingChangeSetIds;
    }
}

function filterChangeSetsForMessage(messageDiv, changeSets = []) {
    const normalized = Array.isArray(changeSets) ? changeSets : [];
    const allowedIds = new Set(getMessageScopedChangeSetIds(messageDiv));
    if (allowedIds.size === 0) {
        return normalized;
    }
    return normalized.filter((changeSet) => allowedIds.has(changeSet?.id));
}

function resolvePendingChangeSetTargetMessage(messageDiv = null) {
    if (messageDiv && messageDiv.isConnected) {
        return messageDiv;
    }
    const messagesRoot = document.getElementById('messages') || document;
    const botMessages = Array.from(messagesRoot.querySelectorAll('.message.bot'));
    const pendingMessage = botMessages.slice().reverse().find((msg) => isMessageApprovalPending(msg));
    if (pendingMessage) return pendingMessage;
    return botMessages[botMessages.length - 1] || document.querySelector('#streamingMessage') || null;
}

let pendingApprovalRestoreTimer = null;

function rehydratePendingApprovalCardsInView(sessionId) {
    const activeSessionId = sessionId || currentSession || currentChatId;
    const messagesRoot = document.getElementById('messages');
    if (!activeSessionId || !messagesRoot) return;
    const botMessages = Array.from(messagesRoot.querySelectorAll('.message.bot'));
    botMessages.forEach((messageDiv) => {
        if (!isMessageApprovalPending(messageDiv)) return;
        ensureApprovalPendingAnchor(messageDiv, { force: true });
        const host = getOrCreateChangeSetHost(messageDiv);
        if (host) {
            delete host.dataset.userPositioned;
            resetChangeSetHostToDefaultPosition(host);
        }
        requestAnimationFrame(() => {
            stabilizeChangeSetHost(messageDiv, {
                preferDefaultPosition: !messageDiv.querySelector('.change-set-origin-anchor')
            });
        });
    });
    const targetMessage = botMessages.slice().reverse().find((messageDiv) => isMessageApprovalPending(messageDiv));
    if (targetMessage) {
        hydratePendingApprovalCards(activeSessionId, targetMessage);
    }
}

function schedulePendingApprovalRestore(sessionId) {
    const activeSessionId = sessionId || currentSession || currentChatId;
    if (!activeSessionId) return;
    if (pendingApprovalRestoreTimer) {
        window.clearTimeout(pendingApprovalRestoreTimer);
    }
    pendingApprovalRestoreTimer = window.setTimeout(() => {
        pendingApprovalRestoreTimer = null;
        rehydratePendingApprovalCardsInView(activeSessionId);
    }, 0);
}

function hydratePendingApprovalCards(sessionId, messageDiv) {
    const targetMessage = resolvePendingChangeSetTargetMessage(messageDiv);
    if (!targetMessage || !isMessageApprovalPending(targetMessage)) return;
    ensureApprovalPendingAnchor(targetMessage, { force: true });
    const host = getOrCreateChangeSetHost(targetMessage);
    if (host) {
        delete host.dataset.userPositioned;
        resetChangeSetHostToDefaultPosition(host);
    }
    requestAnimationFrame(() => {
        stabilizeChangeSetHost(targetMessage, {
            preferDefaultPosition: !targetMessage.querySelector('.change-set-origin-anchor')
        });
    });
    refreshApprovalCompletionState(targetMessage, { scroll: true });
    if (needsPendingChangeSetHydration(targetMessage)) {
        syncPendingChangeSetsForSession(sessionId || currentSession || currentChatId, targetMessage);
    }
}

function rerenderStoredChangeSetCards(messageDiv) {
    if (!messageDiv) return;
    const stored = filterChangeSetsForMessage(messageDiv, Array.isArray(messageDiv._changeSets) ? messageDiv._changeSets : []);
    messageDiv._changeSets = stored;
    if (hasPendingChangeSets(stored)) {
        messageDiv.dataset.approvalPending = 'true';
    } else {
        delete messageDiv.dataset.approvalPending;
    }
    console.log('[change-set-diag] rerenderStoredChangeSetCards called, stored count=', stored.length, 'ids=', stored.map(c => c?.id));
    ensureApprovalPendingAnchor(messageDiv, { force: hasPendingChangeSets(stored) });
    const host = getOrCreateChangeSetHost(messageDiv);
    if (!host) { console.warn('[change-set-diag] rerender: host is null'); return; }
    if (hasPendingChangeSets(stored)) {
        delete host.dataset.userPositioned;
        resetChangeSetHostToDefaultPosition(host);
    }
    const body = host.querySelector(':scope > .change-set-host-body') || host;
    body.style.display = 'flex';
    body.innerHTML = '';
    stored.forEach((changeSet) => renderChangeSetCard(changeSet, messageDiv));
    refreshApprovalCompletionState(messageDiv, { scroll: hasPendingChangeSets(stored) });
}

async function syncPendingChangeSetsForSession(sessionId, messageDiv = null) {
    const activeSessionId = sessionId || currentSession || currentChatId;
    if (!activeSessionId) return;
    try {
        const response = await fetch(`${CONFIG.API_URL}/api/changes/pending?session_id=${encodeURIComponent(activeSessionId)}`);
        const data = await response.json();
        if (!response.ok || !data.success || !Array.isArray(data.change_sets) || data.change_sets.length === 0) {
            const localPending = messageDiv && hasPendingChangeSets(messageDiv._changeSets || []);
            if (localPending) {
                messageDiv.dataset.approvalPending = 'true';
                console.log('[change-set] pending sync returned empty, preserving local pending UI');
                return;
            }
            if (messageDiv) {
                delete messageDiv.dataset.approvalPending;
            }
            await syncFileChangeSummaryForActiveSession(activeSessionId, {
                expanded: fileChangeSummaryState.expanded,
            });
            return;
        }
        const targetMessage = resolvePendingChangeSetTargetMessage(messageDiv);
        if (!targetMessage) {
            await syncFileChangeSummaryForActiveSession(activeSessionId, {
                expanded: fileChangeSummaryState.expanded,
            });
            return;
        }
        const filteredRemoteChangeSets = filterChangeSetsForMessage(targetMessage, data.change_sets || []);
        const mergedChangeSets = mergeChangeSetsById(targetMessage._changeSets || [], filteredRemoteChangeSets);
        if (mergedChangeSets.length > 0) {
            targetMessage._changeSets = mergedChangeSets;
            setMessagePendingChangeSetIds(targetMessage, mergedChangeSets.map((changeSet) => changeSet?.id));
            persistLatestAssistantChangeSets(activeSessionId, mergedChangeSets);
        }
        if (hasPendingChangeSets(targetMessage._changeSets)) {
            targetMessage.dataset.approvalPending = 'true';
        } else {
            delete targetMessage.dataset.approvalPending;
        }
        rerenderStoredChangeSetCards(targetMessage);
        requestAnimationFrame(() => {
            stabilizeChangeSetHost(targetMessage, {
                preferDefaultPosition: !targetMessage.querySelector('.change-set-origin-anchor')
            });
            refreshApprovalCompletionState(targetMessage, { scroll: hasPendingChangeSets(targetMessage._changeSets || []) });
        });
        await syncFileChangeSummaryForActiveSession(activeSessionId, {
            expanded: fileChangeSummaryState.expanded,
        });
    } catch (error) {
        console.error('[change-set] pending sync failed:', error);
        await refreshFileChangeSummaryFromCurrentState(activeSessionId, {
            expanded: fileChangeSummaryState.expanded,
        });
    }
}

function renderUnifiedDiff(diffText) {
    const pre = document.createElement('pre');
    pre.className = 'change-set-diff-view';
    pre.style.margin = '8px 0 0 0';
    pre.style.padding = '10px';
    pre.style.background = 'rgba(0, 0, 0, 0.25)';
    pre.style.borderRadius = '8px';
    pre.style.overflowX = 'auto';
    pre.style.whiteSpace = 'pre-wrap';
    pre.style.wordBreak = 'break-word';
    const lines = String(diffText || '').split('\n');
    const html = lines.map((line) => {
        let color = 'var(--text-primary)';
        if (line.startsWith('+') && !line.startsWith('+++')) {
            color = '#4ade80';
        } else if (line.startsWith('-') && !line.startsWith('---')) {
            color = '#f87171';
        } else if (line.startsWith('@@')) {
            color = '#fbbf24';
        }
        return `<div style="color:${color};">${escapeHtml(line)}</div>`;
    }).join('');
    pre.innerHTML = html || '<div style="color: var(--text-secondary);">(empty diff)</div>';
    return pre;
}

function getChangeSetState(changeSet) {
    if (!changeSet?.id) return { status: 'pending' };
    const cached = changeSetStateCache.get(changeSet.id) || {};
    return {
        status: cached.status || changeSet.status || 'pending',
        busy: Boolean(cached.busy),
        error: cached.error || '',
        pendingAction: cached.pendingAction || '',
        selectedAction: cached.selectedAction || changeSet.selected_action || ''
    };
}

function getChangeSetStatusMeta(status) {
    if (status === 'applied') return { text: '已应用', color: '#4ade80', locked: true };
    if (status === 'rejected') return { text: '已拒绝', color: '#f87171', locked: true };
    if (status === 'reverted') return { text: '已回滚', color: '#60a5fa', locked: true };
    return { text: '待审批', color: '#fbbf24', locked: false };
}

function getChangeSetTypeMeta(changeSet) {
    if ((changeSet?.type || 'direct') === 'rollback_review') {
        return {
            title: '⚠️ 冲突审查',
            subtitle: '文件已被外部修改，请审查后决定是否应用此回滚',
            borderColor: 'rgba(251, 146, 60, 0.75)',
            background: 'rgba(251, 146, 60, 0.08)'
        };
    }
    return {
        title: '🧾 待审批修改',
        subtitle: '',
        borderColor: '1px solid rgba(255,255,255,0.12)',
        background: 'rgba(255,255,255,0.03)'
    };
}

function setChangeSetState(changeSetId, patch = {}) {
    const prev = changeSetStateCache.get(changeSetId) || { status: 'pending', busy: false, error: '', pendingAction: '', selectedAction: '' };
    const next = { ...prev, ...patch };
    changeSetStateCache.set(changeSetId, next);
    // Persist status change to localStorage for F5 refresh
    document.querySelectorAll(`[data-change-set-id="${changeSetId}"]`).forEach((card) => {
        const messageDiv = card.closest('.message');
        if (!messageDiv || !Array.isArray(messageDiv._changeSets)) return;
        messageDiv._changeSets = messageDiv._changeSets.map((item) => {
            if (item?.id !== changeSetId) return item;
            return {
                ...item,
                status: next.status || item.status,
                last_error: next.error || item.last_error || '',
                selected_action: next.selectedAction || item.selected_action || ''
            };
        });
        const chatId = currentChatId || currentSession;
        if (chatId) {
            try {
                const saved = localStorage.getItem(`chat_${chatId}`);
                const chatHistory = saved ? JSON.parse(saved) : [];
                for (let i = chatHistory.length - 1; i >= 0; i--) {
                    if (chatHistory[i]?.role === 'assistant' && Array.isArray(chatHistory[i].change_sets)) {
                        chatHistory[i].change_sets = messageDiv._changeSets.map(item => ({ ...item }));
                        localStorage.setItem(`chat_${chatId}`, JSON.stringify(chatHistory));
                        break;
                    }
                }
            } catch (e) { /* ignore */ }
        }
    });
    updateChangeSetCards(changeSetId);
    return next;
}

function showChangeSetError(changeSetId, message) {
    setChangeSetState(changeSetId, { error: message || '' });
    if (message) {
        window.setTimeout(() => {
            const current = changeSetStateCache.get(changeSetId);
            if (current?.error === message) {
                setChangeSetState(changeSetId, { error: '' });
            }
        }, 3000);
    }
}

function setChangeSetButtonAppearance(button, options = {}) {
    if (!button) return;
    const {
        disabled = false,
        active = false,
        faded = false,
        accent = 'rgba(148, 163, 184, 0.35)'
    } = options;
    button.disabled = disabled;
    button.style.display = '';
    button.style.opacity = faded ? (active ? '0.76' : '0.42') : '1';
    button.style.filter = faded && !active ? 'grayscale(0.18)' : 'none';
    button.style.cursor = disabled ? 'not-allowed' : 'pointer';
    button.style.pointerEvents = disabled ? 'auto' : 'auto';
    button.style.boxShadow = active ? `0 0 0 1px ${accent} inset, 0 0 14px ${accent}` : '';
    button.style.transform = active ? 'translateY(-1px)' : '';
}

function updateChangeSetCards(changeSetId) {
    const state = changeSetStateCache.get(changeSetId) || { status: 'pending', busy: false, error: '', pendingAction: '', selectedAction: '' };
    const meta = getChangeSetStatusMeta(state.status);
    document.querySelectorAll(`[data-change-set-id="${changeSetId}"]`).forEach((card) => {
        card.dataset.status = state.status;
        const status = card.querySelector('.change-set-status');
        if (status) {
            status.textContent = state.busy ? '处理中...' : meta.text;
            status.style.color = state.busy ? '#fbbf24' : meta.color;
        }
        const errorEl = card.querySelector('.change-set-error');
        if (errorEl) {
            errorEl.textContent = state.error || '';
            errorEl.style.display = state.error ? 'block' : 'none';
        }
        const acceptBtn = card.querySelector('[data-change-set-action="accept"]');
        const rejectBtn = card.querySelector('[data-change-set-action="reject"]');
        const alwaysAcceptBtn = card.querySelector('[data-change-set-action="always_accept"]');
        const alwaysRejectBtn = card.querySelector('[data-change-set-action="always_reject"]');
        const selectedAction = state.selectedAction || '';
        const isLocked = Boolean(meta.locked);
        const isBusy = Boolean(state.busy);
        const buttonConfigs = [
            { button: acceptBtn, action: 'accept', accent: 'rgba(74, 222, 128, 0.42)', busyText: '处理中...', text: '✅ 接受这一次(Accept)' },
            { button: rejectBtn, action: 'reject', accent: 'rgba(248, 113, 113, 0.42)', busyText: '处理中...', text: '❌ 拒绝这一次(Reject)' },
            { button: alwaysAcceptBtn, action: 'always_accept', accent: 'rgba(74, 222, 128, 0.34)', busyText: '处理中...', text: '🔄 本次会话一直接受' },
            { button: alwaysRejectBtn, action: 'always_reject', accent: 'rgba(248, 113, 113, 0.34)', busyText: '处理中...', text: '🚫 本次会话一直拒绝' }
        ];
        buttonConfigs.forEach(({ button, action, accent, busyText, text }) => {
            if (!button) return;
            const active = selectedAction === action;
            button.textContent = isBusy && active ? busyText : text;
            setChangeSetButtonAppearance(button, {
                disabled: isBusy || isLocked,
                active,
                faded: isLocked,
                accent
            });
        });
    });
}

async function acceptChangeSet(changeSetId, button) {
    const selectedAction = button?.dataset?.changeSetAction || 'accept';
    setChangeSetState(changeSetId, { busy: true, pendingAction: 'accept', selectedAction, error: '' });
    try {
        const response = await fetch(`${CONFIG.API_URL}/api/changes/${encodeURIComponent(changeSetId)}/accept`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        const data = await response.json();
        if (!response.ok || !data.success) {
            const message = data.error || 'Accept failed';
            showChangeSetError(changeSetId, `操作失败：${message}`);
            setChangeSetState(changeSetId, { busy: false, pendingAction: '', selectedAction: '' });
            throw new Error(message);
        }
        const nextStatus = data.change_set?.status || 'applied';
        setChangeSetState(changeSetId, { status: nextStatus, busy: false, pendingAction: '', selectedAction, error: '' });
        const messageDiv = button?.closest('.message') || document.querySelector(`[data-change-set-id="${changeSetId}"]`)?.closest('.message');
        if (messageDiv) _resumeAfterApproval(messageDiv, 'accepted');
        syncFileChangeSummaryForActiveSession(currentSession || currentChatId, {
            expanded: fileChangeSummaryState.expanded,
        }).catch((error) => {
            console.warn('[change-set] summary refresh after accept failed:', error);
        });
        refreshTaskStatusBar(currentSession || currentChatId).catch((error) => {
            console.warn('[change-set] task status refresh after accept failed:', error);
        });
    } catch (error) {
        console.error('[change-set] accept failed:', error);
    }
}

async function rejectChangeSet(changeSetId, button) {
    const selectedAction = button?.dataset?.changeSetAction || 'reject';
    setChangeSetState(changeSetId, { busy: true, pendingAction: 'reject', selectedAction, error: '' });
    try {
        const response = await fetch(`${CONFIG.API_URL}/api/changes/${encodeURIComponent(changeSetId)}/reject`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        const data = await response.json();
        if (!response.ok || !data.success) {
            const message = data.error || 'Reject failed';
            if (data.review_change_set && button) {
                const messageDiv = button.closest('.message');
                if (messageDiv) {
                    renderChangeSetCard(data.review_change_set, messageDiv);
                }
            }
            showChangeSetError(changeSetId, `操作失败：${message}`);
            setChangeSetState(changeSetId, { busy: false, pendingAction: '', selectedAction: '' });
            throw new Error(message);
        }
        const nextStatus = data.change_set?.status || 'rejected';
        setChangeSetState(changeSetId, { status: nextStatus, busy: false, pendingAction: '', selectedAction, error: '' });
        const messageDiv = button?.closest('.message') || document.querySelector(`[data-change-set-id="${changeSetId}"]`)?.closest('.message');
        if (messageDiv) _resumeAfterApproval(messageDiv, 'rejected');
        syncFileChangeSummaryForActiveSession(currentSession || currentChatId, {
            expanded: fileChangeSummaryState.expanded,
        }).catch((error) => {
            console.warn('[change-set] summary refresh after reject failed:', error);
        });
        refreshTaskStatusBar(currentSession || currentChatId).catch((error) => {
            console.warn('[change-set] task status refresh after reject failed:', error);
        });
    } catch (error) {
        console.error('[change-set] reject failed:', error);
    }
}

// After approval action, check if all change_sets are resolved.
// If so, resume the agentic loop by sending a continuation message.
function _resumeAfterApproval(messageDiv, action) {
    if (!messageDiv || !isMessageApprovalPending(messageDiv)) return;
    if (messageDiv.dataset.approvalResuming === 'true') return;
    const changeSets = messageDiv._changeSets || [];
    const hasPending = changeSets.some(cs => {
        const st = changeSetStateCache.get(cs.id);
        const status = st?.status || cs.status || 'pending';
        return status === 'pending';
    });
    if (hasPending) return; // Still have pending change sets, don't resume yet
    // All resolved — clear approvalPending and resume
    delete messageDiv.dataset.approvalPending;
    const autoAction = getChangeSetAutoAction();
    if (action === 'rejected') {
        const rejectRecoveryCount = Number(messageDiv.dataset.approvalRejectRecoveryCount || '0') + 1;
        messageDiv.dataset.approvalRejectRecoveryCount = String(rejectRecoveryCount);
        const recoveryLimit = autoAction === 'reject' ? 2 : 4;
        if (rejectRecoveryCount > recoveryLimit) {
            const completeDiv = messageDiv.querySelector('.response-complete');
            if (completeDiv) {
                completeDiv.innerHTML = '<i class="fas fa-ban"></i><span>连续拒绝过多，已停止自动恢复，请切换到 Ask / Plan 重新规划</span>';
            }
            delete messageDiv.dataset.approvalResuming;
            return;
        }
    } else {
        delete messageDiv.dataset.approvalRejectRecoveryCount;
    }
    // Update the "⏳ 等待审批" → "回答已完成"
    const completeDiv = messageDiv.querySelector('.response-complete');
    if (completeDiv) {
        completeDiv.innerHTML = action === 'accepted'
            ? '<i class="fas fa-spinner fa-spin"></i><span>已审批，继续处理中</span>'
            : (autoAction === 'reject'
                ? '<i class="fas fa-ban"></i><span>已拒绝，本会话转为只读分析中</span>'
                : '<i class="fas fa-spinner fa-spin"></i><span>已拒绝，正在重新分析替代方案</span>');
    }
    const bottomToolbar = messageDiv.querySelector('.message-bottom-toolbar');
    if (bottomToolbar && !bottomToolbar.querySelector('.fix-bug-container')) {
        const fixButtonDiv = document.createElement('div');
        fixButtonDiv.className = 'fix-bug-container status-normal';
        fixButtonDiv.setAttribute('onclick', 'openFixPanel()');
        fixButtonDiv.setAttribute('title', '打开系统状态面板');
        fixButtonDiv.innerHTML = '<i class="fas fa-shield-alt"></i><span>系统状态</span>';
        if (completeDiv && completeDiv.parentNode === bottomToolbar) {
            completeDiv.insertAdjacentElement('afterend', fixButtonDiv);
        } else {
            bottomToolbar.insertBefore(fixButtonDiv, bottomToolbar.firstChild);
        }
    }
    const resumeMsg = buildApprovalResumeMessage(messageDiv, action);
    messageDiv.dataset.approvalResuming = 'true';

    let resumeAttempts = 0;
    const tryResume = () => {
        if (isProcessing) {
            resumeAttempts += 1;
            if (resumeAttempts <= 40) {
                window.setTimeout(tryResume, 50);
                return;
            }
            delete messageDiv.dataset.approvalResuming;
            if (completeDiv) {
                completeDiv.innerHTML = '<i class="fas fa-exclamation-triangle"></i><span>恢复超时，请重试</span>';
            }
            return;
        }
        isProcessing = true;
        processingStartTime = Date.now();
        updateSendButton();
        sendViaAPI(resumeMsg, [], {
            appendToMessage: messageDiv,
            hiddenContinuation: true
        }).catch((error) => {
            console.error('[change-set] resume after approval failed:', error);
            addMessageToUI('bot', `❌ 审批后恢复失败: ${error.message}`);
        }).finally(() => {
            delete messageDiv.dataset.approvalResuming;
            hideTypingIndicator();
            isProcessing = false;
            processingStartTime = null;
            updateSendButton();
        });
    };

    tryResume();
}

function renderChangeSetCard(changeSet, messageDiv) {
    console.log('[change-set-diag] renderChangeSetCard called, id=', changeSet?.id, 'messageDiv=', !!messageDiv, 'full=', JSON.stringify(changeSet)?.substring(0, 200));
    if (!changeSet || !changeSet.id || !messageDiv) {
        console.warn('[change-set-diag] EARLY RETURN: changeSet=', !!changeSet, 'id=', changeSet?.id, 'messageDiv=', !!messageDiv);
        return;
    }
    const existingIds = getMessageScopedChangeSetIds(messageDiv);
    setMessagePendingChangeSetIds(messageDiv, existingIds.concat(changeSet.id));
    rememberChangeSetForMessage(messageDiv, changeSet);
    const state = getChangeSetState(changeSet);
    const mergedState = { ...state, status: changeSet.status || state.status || 'pending' };
    if (mergedState.status === 'pending') {
        messageDiv.dataset.approvalPending = 'true';
    }
    changeSetStateCache.set(changeSet.id, mergedState);
    const host = getOrCreateChangeSetHost(messageDiv);
    console.log('[change-set-diag] getOrCreateChangeSetHost returned:', !!host);
    if (!host) { console.warn('[change-set-diag] host is null, aborting card render'); return; }
    const existing = host.querySelector(`[data-change-set-id="${changeSet.id}"]`);
    if (existing) {
        updateChangeSetCards(changeSet.id);
        requestAnimationFrame(() => positionChangeSetHostAtOrigin(messageDiv, host));
        return;
    }
    const statusMeta = getChangeSetStatusMeta(mergedState.status);
    const typeMeta = getChangeSetTypeMeta(changeSet);
    const card = document.createElement('div');
    card.className = 'change-set-card';
    card.dataset.changeSetId = changeSet.id;
    card.dataset.status = mergedState.status || 'pending';
    card.dataset.changeSetType = changeSet.type || 'direct';
    card.style.marginTop = '10px';
    card.style.padding = '12px';
    card.style.border = typeMeta.borderColor.startsWith('1px') ? typeMeta.borderColor : `1px solid ${typeMeta.borderColor}`;
    card.style.borderRadius = '10px';
    card.style.background = typeMeta.background;

    const header = document.createElement('div');
    header.style.display = 'flex';
    header.style.justifyContent = 'space-between';
    header.style.alignItems = 'center';
    header.style.gap = '12px';
    header.innerHTML = `
        <div>
            <div style="font-weight: 600;">${typeMeta.title}</div>
            <div style="font-size: 12px; color: var(--text-secondary);">${escapeHtml(changeSet.id)}</div>
        </div>
        <div class="change-set-status" style="font-size: 12px; color: ${statusMeta.color};">${statusMeta.text}</div>
    `;
    const titleBlock = header.firstElementChild;
    if (titleBlock) {
        titleBlock.style.cursor = 'context-menu';
        titleBlock.title = '右键打开窗口菜单';
    }
    card.appendChild(header);

    if (typeMeta.subtitle) {
        const typeNotice = document.createElement('div');
        typeNotice.className = 'change-set-type-notice';
        typeNotice.style.marginTop = '8px';
        typeNotice.style.fontSize = '12px';
        typeNotice.style.color = '#fdba74';
        typeNotice.textContent = typeMeta.subtitle;
        card.appendChild(typeNotice);
    }

    const errorEl = document.createElement('div');
    errorEl.className = 'change-set-error';
    errorEl.style.display = state.error ? 'block' : 'none';
    errorEl.style.marginTop = '8px';
    errorEl.style.fontSize = '12px';
    errorEl.style.color = '#f87171';
    errorEl.textContent = state.error || '';
    card.appendChild(errorEl);

    const files = Array.isArray(changeSet.files) ? changeSet.files : [];
    files.forEach((file) => {
        const section = document.createElement('div');
        section.style.marginTop = '10px';
        section.innerHTML = `
            <div style="font-size: 13px; font-weight: 600; margin-bottom: 4px;">${escapeHtml(file.path || '')}</div>
            <div style="font-size: 12px; color: var(--text-secondary);">${escapeHtml(file.summary || '')}</div>
        `;
        section.appendChild(renderUnifiedDiff(file.diff || ''));
        card.appendChild(section);
    });

    const actions = document.createElement('div');
    actions.style.display = 'flex';
    actions.style.gap = '8px';
    actions.style.marginTop = '12px';
    const autoActionSessionId = getChangeSetAutoActionSessionId();

    const acceptBtn = document.createElement('button');
    acceptBtn.className = 'quick-btn success';
    acceptBtn.dataset.changeSetAction = 'accept';
    acceptBtn.textContent = '✅ 接受这一次(Accept)';
    acceptBtn.onclick = () => acceptChangeSet(changeSet.id, acceptBtn);

    const rejectBtn = document.createElement('button');
    rejectBtn.className = 'quick-btn danger';
    rejectBtn.dataset.changeSetAction = 'reject';
    rejectBtn.textContent = '❌ 拒绝这一次(Reject)';
    rejectBtn.onclick = () => rejectChangeSet(changeSet.id, rejectBtn);

    const alwaysAcceptBtn = document.createElement('button');
    alwaysAcceptBtn.className = 'quick-btn';
    alwaysAcceptBtn.dataset.changeSetAction = 'always_accept';
    alwaysAcceptBtn.style.cssText = 'background:rgba(34,197,94,0.15);color:#4ade80;border:1px solid rgba(34,197,94,0.3);font-size:11px;';
    alwaysAcceptBtn.textContent = '🔄 本次会话一直接受';
    alwaysAcceptBtn.title = '自动接受本次会话中所有后续修改';
    if (getChangeSetAutoAction(autoActionSessionId) === 'accept') {
        alwaysAcceptBtn.style.background = 'rgba(34,197,94,0.4)';
    }
    alwaysAcceptBtn.onclick = () => {
        setChangeSetAutoAction('accept', autoActionSessionId);
        alwaysAcceptBtn.style.background = 'rgba(34,197,94,0.4)';
        alwaysRejectBtn.style.background = 'rgba(248,113,113,0.15)';
        acceptChangeSet(changeSet.id, alwaysAcceptBtn);
    };

    const alwaysRejectBtn = document.createElement('button');
    alwaysRejectBtn.className = 'quick-btn';
    alwaysRejectBtn.dataset.changeSetAction = 'always_reject';
    alwaysRejectBtn.style.cssText = 'background:rgba(248,113,113,0.15);color:#f87171;border:1px solid rgba(248,113,113,0.3);font-size:11px;';
    alwaysRejectBtn.textContent = '🚫 本次会话一直拒绝';
    alwaysRejectBtn.title = '自动拒绝本次会话中所有后续修改';
    if (getChangeSetAutoAction(autoActionSessionId) === 'reject') {
        alwaysRejectBtn.style.background = 'rgba(248,113,113,0.4)';
    }
    alwaysRejectBtn.onclick = () => {
        setChangeSetAutoAction('reject', autoActionSessionId);
        alwaysRejectBtn.style.background = 'rgba(248,113,113,0.4)';
        alwaysAcceptBtn.style.background = 'rgba(34,197,94,0.15)';
        rejectChangeSet(changeSet.id, alwaysRejectBtn);
    };

    actions.appendChild(acceptBtn);
    actions.appendChild(rejectBtn);
    actions.appendChild(alwaysAcceptBtn);
    actions.appendChild(alwaysRejectBtn);
    card.appendChild(actions);
    const hostBody = host.querySelector(':scope > .change-set-host-body') || host;
    hostBody.appendChild(card);
    updateChangeSetCards(changeSet.id);
    requestAnimationFrame(() => positionChangeSetHostAtOrigin(messageDiv, host));
    refreshApprovalCompletionState(messageDiv, { scroll: mergedState.status === 'pending' });
    console.log('[change-set-diag] Card appended to host, host.children=', host.children.length, 'host.parentNode=', !!host.parentNode);
}

// 将思考过程转换为 HTML
function formatThinkingToHTML(html, thinkingBlocks) {
    let result = html;
    
    // 辅助函数：检测文件语言类型
    function detectLanguage(filePath) {
        if (!filePath) return null;
        const ext = filePath.split('.').pop().toLowerCase();
        const langMap = {
            'py': 'python', 'js': 'javascript', 'ts': 'typescript',
            'jsx': 'javascript', 'tsx': 'typescript',
            'html': 'html', 'css': 'css', 'scss': 'scss',
            'json': 'json', 'yaml': 'yaml', 'yml': 'yaml',
            'md': 'markdown', 'mdx': 'markdown',
            'sh': 'bash', 'bash': 'bash',
            'java': 'java', 'go': 'go', 'rs': 'rust',
            'cpp': 'cpp', 'c': 'c', 'h': 'c',
            'sql': 'sql', 'xml': 'xml',
            'vue': 'vue', 'svelte': 'svelte'
        };
        return langMap[ext] || null;
    }
    
    // 辅助函数：应用代码高亮
    function highlightCode(code, language) {
        if (!code) return '';
        const escaped = escapeHtml(code);
        if (language && typeof hljs !== 'undefined') {
            try {
                return hljs.highlight(escaped, { language }).value;
            } catch (e) {
                // 忽略高亮错误
            }
        }
        return escaped;
    }
    
    // 辅助函数：生成行号
    function generateLineNumbers(code) {
        if (!code) return '';
        const lines = code.split('\n');
        return lines.map((line, i) => 
            `<span class="diff-line-number">${i + 1}</span>${escapeHtml(line)}`
        ).join('\n');
    }
    
    // 如果有思考块，替换为 HTML
    if (thinkingBlocks && thinkingBlocks.length > 0) {
        thinkingBlocks.forEach(block => {
            let thinkingHTML;
            
            if (block.isToolCall && block.toolInfo) {
                const info = block.toolInfo;
                const lang = detectLanguage(info.filePath);
                
                if ((info.type === 'edit' || info.type === 'multi_edit') && info.filePath) {
                    // 文件编辑 - 显示 diff 风格
                    const oldHighlighted = info.oldContent ? highlightCode(info.oldContent, lang) : '';
                    const newHighlighted = info.newContent ? highlightCode(info.newContent, lang) : '';
                    
                    thinkingHTML = `<div class="tool-call-block file-edit-block">
                        <div class="tool-call-label" onclick="toggleThinking(this)">
                            <i class="fas fa-file-code"></i>
                            <span class="file-path">${escapeHtml(info.filePath)}</span>
                            <span class="edit-badge">${info.type === 'multi_edit' ? '多次编辑' : '编辑'}</span>
                            <i class="fas fa-chevron-down toggle-icon"></i>
                        </div>
                        ${info.explanation ? `<div class="edit-explanation"><i class="fas fa-info-circle"></i> ${escapeHtml(info.explanation)}</div>` : ''}
                        <div class="tool-call-content">
                            ${info.oldContent ? `<div class="diff-section diff-removed"><div class="diff-header"><i class="fas fa-minus"></i> 删除 (${info.oldContent.split('\n').length} 行)</div><pre><code>${oldHighlighted}</code></pre></div>` : ''}
                            ${info.newContent ? `<div class="diff-section diff-added"><div class="diff-header"><i class="fas fa-plus"></i> 新增 (${info.newContent.split('\n').length} 行)</div><pre><code>${newHighlighted}</code></pre></div>` : ''}
                            ${!info.oldContent && !info.newContent ? `<pre>${escapeHtml(info.raw)}</pre>` : ''}
                        </div>
                    </div>`;
                } else if (info.type === 'write' && info.filePath) {
                    // 文件创建
                    const contentHighlighted = info.newContent ? highlightCode(info.newContent, lang) : escapeHtml(info.raw);
                    thinkingHTML = `<div class="tool-call-block file-write-block">
                        <div class="tool-call-label" onclick="toggleThinking(this)">
                            <i class="fas fa-file-plus"></i>
                            <span class="file-path">${escapeHtml(info.filePath)}</span>
                            <span class="write-badge">新建</span>
                            <i class="fas fa-chevron-down toggle-icon"></i>
                        </div>
                        <div class="tool-call-content"><pre><code>${contentHighlighted}</code></pre></div>
                    </div>`;
                } else if (info.type === 'read' && info.filePath) {
                    // 文件读取
                    thinkingHTML = `<div class="tool-call-block file-read-block">
                        <div class="tool-call-label" onclick="toggleThinking(this)">
                            <i class="fas fa-file-alt"></i>
                            <span class="file-path">${escapeHtml(info.filePath)}</span>
                            <span class="read-badge">读取</span>
                            <i class="fas fa-chevron-down toggle-icon"></i>
                        </div>
                        <div class="tool-call-content"><pre>${escapeHtml(info.raw)}</pre></div>
                    </div>`;
                } else if (info.type === 'search') {
                    // 搜索
                    thinkingHTML = `<div class="tool-call-block search-block">
                        <div class="tool-call-label" onclick="toggleThinking(this)">
                            <i class="fas fa-search"></i>
                            <span>搜索</span>
                            ${info.command ? `<span class="search-query">"${escapeHtml(info.command)}"</span>` : ''}
                            <i class="fas fa-chevron-down toggle-icon"></i>
                        </div>
                        <div class="tool-call-content"><pre>${escapeHtml(info.raw)}</pre></div>
                    </div>`;
                } else if (info.type === 'exec' && info.command) {
                    // 命令执行
                    thinkingHTML = `<div class="tool-call-block exec-block">
                        <div class="tool-call-label" onclick="toggleThinking(this)">
                            <i class="fas fa-terminal"></i>
                            <span>执行命令</span>
                            <i class="fas fa-chevron-down toggle-icon"></i>
                        </div>
                        <div class="tool-call-content"><code>${escapeHtml(info.command)}</code></div>
                    </div>`;
                } else if (info.type === 'task_planner') {
                    // 任务规划
                    thinkingHTML = `<div class="tool-call-block task-block">
                        <div class="tool-call-label" onclick="toggleThinking(this)">
                            <i class="fas fa-tasks"></i>
                            <span>任务规划</span>
                            <i class="fas fa-chevron-down toggle-icon"></i>
                        </div>
                        <div class="tool-call-content"><pre>${escapeHtml(info.raw)}</pre></div>
                    </div>`;
                } else {
                    // 默认工具调用样式
                    thinkingHTML = `<div class="tool-call-block expanded">
                        <div class="tool-call-label" onclick="toggleThinking(this)">
                            <i class="fas fa-cog"></i>
                            <span>工具调用</span>
                            ${info.type ? `<span class="tool-type-badge">${escapeHtml(info.type)}</span>` : ''}
                            <i class="fas fa-chevron-down toggle-icon"></i>
                        </div>
                        <div class="tool-call-content"><pre>${escapeHtml(block.content)}</pre></div>
                    </div>`;
                }
            } else {
                // 普通思考块样式
                thinkingHTML = `<div class="thinking-block expanded">
                    <div class="thinking-label" onclick="toggleThinking(this)">
                        <i class="fas fa-brain"></i>
                        <span>思考过程</span>
                        <i class="fas fa-chevron-down toggle-icon"></i>
                    </div>
                    <div class="thinking-content">${block.content}</div>
                </div>`;
            }
            result = result.replace(block.placeholder, thinkingHTML);
        });
    }
    
    // 清理任何残留的占位符（防止显示原始文本）
    result = result.replace(/__THINKING_BLOCK_\d+__/g, '');
    result = result.replace(/THINKING_BLOCK_\d+/g, '');
    result = result.replace(/__THINKING_BLOCK_\d+/g, '');
    result = result.replace(/THINKING_BLOCK_\d+__/g, '');
    
    return result;
}

// 已禁用：此函数会错误地压缩空行，导致代码块之间的空行丢失
// 直接返回原始文本，保持后端返回的格式
function insertLineBreaksAfterPunctuation(text) {
    return text;
}

// 修复双竖线表格格式为标准Markdown表格
function fixDoublePipeTable(text) {
    const lines = text.split('\n');
    const result = [];
    
    const hasDoublePipe = (line) => {
        return line.includes('||');
    };
    
    const isTableLine = (line) => {
        const trimmed = line.trim();
        return trimmed.startsWith('|') && trimmed.endsWith('|');
    };
    
    for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        
        if (isTableLine(line) && hasDoublePipe(line)) {
            // 将双竖线转换为单竖线
            const converted = line.trim().replace(/\|\|/g, '|');
            result.push(converted);
        } else {
            result.push(line);
        }
    }
    
    return result.join('\n');
}

// 修复未闭合的代码块 - DISABLED
// 后端返回的格式已经是正确的，不需要修复
function fixUnclosedCodeBlocks(text) {
    // 直接返回原文，不做任何处理
    return text;
}

/**
 * 预处理代码围栏：当外层代码块内部包含 ``` 时，将外层围栏升级为更长的围栏。
 * 借鉴 llama.cpp 的 detectIncompleteCodeBlock（奇偶配对）思路，并采用 CommonMark
 * 标准的 "更长围栏包裹内部围栏" 方案。
 *
 * 算法核心（Stack-based fence pairing）：
 *   - ```lang  → 视为 "开围栏"（push）
 *   - ```      → 如果栈非空则视为 "闭围栏"（pop），栈空时则视为 "开围栏"（push）
 *   - 当栈归零时，最外层代码块才真正闭合
 *   - 如果闭合的块内部出现了 ≥ 外层长度的围栏，升级外层为 innerMax+1
 *
 * 原理：CommonMark 规范规定，关闭围栏的 backtick 数必须 ≥ 打开围栏的。
 * 所以 ```` 打开的块不会被内部的 ``` 关闭。
 */
function preprocessCodeFences(text) {
    if (!text || typeof text !== 'string') return text || '';

    const lines = text.split('\n');

    // ── Pass 1: 收集所有围栏行的元信息 ──
    const fences = [];  // { lineIdx, backtickLen, hasInfoString, indent, langTag }
    for (let i = 0; i < lines.length; i++) {
        const m = lines[i].match(/^(\s{0,3})(`{3,})(.*)?$/);
        if (m) {
            const afterBt = (m[3] || '').trim();
            fences.push({
                lineIdx: i,
                backtickLen: m[2].length,
                hasInfoString: afterBt.length > 0,
                indent: m[1],
                langTag: afterBt
            });
        }
    }
    if (fences.length < 2) return text;  // 0 或 1 个围栏行无需处理

    // ── Pass 2: 栈配对，找出需要升级的外层块 ──
    const stack = [];    // 栈元素：fence 索引
    const upgrades = []; // { openFi, closeFi, newLen }
    let blockOpener = -1; // 最外层开围栏的 fence 索引

    for (let fi = 0; fi < fences.length; fi++) {
        const f = fences[fi];
        if (f.hasInfoString) {
            // ```lang → push（开围栏）
            if (stack.length === 0) blockOpener = fi;
            stack.push(fi);
        } else {
            // ``` (裸围栏)
            if (stack.length > 0) {
                stack.pop();
                if (stack.length === 0 && blockOpener >= 0) {
                    // 最外层闭合 — 检查内部围栏是否需要升级
                    const openBtLen = fences[blockOpener].backtickLen;
                    let innerMax = 0;
                    for (let j = blockOpener + 1; j < fi; j++) {
                        if (fences[j].backtickLen > innerMax) innerMax = fences[j].backtickLen;
                    }
                    if (innerMax >= openBtLen) {
                        upgrades.push({ openFi: blockOpener, closeFi: fi, newLen: innerMax + 1 });
                    }
                    blockOpener = -1;
                }
            } else {
                // 栈空 → 裸围栏作为开围栏
                blockOpener = fi;
                stack.push(fi);
            }
        }
    }

    // 处理流式场景：文件末尾代码块未闭合
    if (stack.length > 0) {
        const openFi = stack[0];
        const openBtLen = fences[openFi].backtickLen;
        let innerMax = 0;
        for (let j = openFi + 1; j < fences.length; j++) {
            if (fences[j].backtickLen > innerMax) {
                innerMax = fences[j].backtickLen;
            }
        }
        if (innerMax >= openBtLen) {
            upgrades.push({ openFi, closeFi: -1, newLen: innerMax + 1 });
        }
    }

    if (upgrades.length === 0) return text;

    // ── Pass 3: 应用升级 ──
    const result = [...lines];
    for (const up of upgrades) {
        const newBt = '`'.repeat(up.newLen);
        const openF = fences[up.openFi];
        result[openF.lineIdx] = openF.indent + newBt +
            (openF.langTag ? ' ' + openF.langTag : '');
        if (up.closeFi >= 0) {
            const closeF = fences[up.closeFi];
            result[closeF.lineIdx] = closeF.indent + newBt;
        }
    }

    return result.join('\n');
}

function parseMarkdownToHtml(markdownText) {
    configureMarkedRenderer();
    return marked.parse(markdownText);
}

function applyMarkdownPostProcessors(html, context = {}) {
    const processors = [
        (value, ctx) => formatThinkingToHTML(value, ctx.thinkingBlocks || []),
        (value) => value.replace(/<table>/g, '<table class="markdown-table">')
    ];

    return processors.reduce((acc, processor) => {
        try {
            return processor(acc, context);
        } catch (error) {
            console.warn('[applyMarkdownPostProcessors] processor failed:', error);
            return acc;
        }
    }, html);
}

function sanitizeMarkdownHtml(html) {
    return DOMPurify.sanitize(html, MARKDOWN_SANITIZE_OPTIONS);
}

// 全局 marked renderer 配置（只初始化一次）
let markedRendererConfigured = false;

function configureMarkedRenderer() {
    if (markedRendererConfigured) return;
    
    console.log('[configureMarkedRenderer] Initializing marked configuration');
    
    marked.setOptions({
        gfm: true,
        breaks: true
    });

    // marked v9+ 不再支持 new marked.Renderer()，直接使用对象
    const renderer = {};
    
    // 覆盖 code 渲染器 - marked v9 使用 token 对象
    renderer.code = function(token, infostring, escaped) {
        // marked v9 可能传递 token 对象或 (code, infostring, escaped) 参数
        let codeText, language;
        
        console.log('[marked.code] Arguments:', arguments.length, 'typeof token:', typeof token);
        
        if (typeof token === 'object' && token !== null) {
            // marked v5+ 格式：token 是对象
            // 尝试多种属性名：lang, info, infostring
            codeText = token.text || token.code || '';
            language = token.lang || token.info || token.infostring || '';
            console.log('[marked.code] Token object:', JSON.stringify({text: codeText.substring(0,50), lang: language}));
        } else if (typeof token === 'string') {
            // marked v4 及更早版本：code(code, infostring, escaped)
            codeText = token;
            language = infostring || '';
            console.log('[marked.code] String format: code length=' + codeText.length + ', lang=' + language);
        } else {
            codeText = '';
            language = '';
        }
        
        console.log('[marked.code] Extracted lang:', language, 'text length:', codeText.length);
        
        // 清理代码内容
        const cleanedCode = codeText.replace(/^\n+|\n+$/g, '');
        
        // 过滤空代码块：如果清理后内容为空或只有空白，不渲染代码块
        if (!cleanedCode || cleanedCode.trim().length === 0) {
            console.log('[marked.code] Skipping empty code block');
            return '';
        }
        
        // 从代码注释中提取更精确的文件类型标签
        // 例如：// C Header (.h) -> h, // C++ Header (.hpp) -> hpp
        function extractFileTypeFromCode(code, originalLang) {
            const firstLine = code.split('\n')[0] || '';
            console.log('[extractFileTypeFromCode] firstLine:', firstLine, 'originalLang:', originalLang);
            
            // 检查 C Header (.h) 注释
            if (/C\s*Header\s*\(\.h\)/i.test(firstLine)) {
                console.log('[extractFileTypeFromCode] Detected C Header (.h) -> h');
                return 'h';
            }
            // 检查 C++ Header (.hpp) 注释
            if (/C\+\+\s*Header\s*\(\.hpp\)/i.test(firstLine)) {
                console.log('[extractFileTypeFromCode] Detected C++ Header (.hpp) -> hpp');
                return 'hpp';
            }
            // 检查 CFG 配置文件注释 - 需要精确匹配，避免误匹配 .ini, .conf, .cfg 这样的列表
            const cfgMatch = firstLine.match(/CFG\s*配置文件|\.cfg\s*[)\]]/i);
            if (cfgMatch && !firstLine.match(/\.ini.*\.cfg/i)) {
                console.log('[extractFileTypeFromCode] Detected CFG -> cfg');
                return 'cfg';
            }
            // 检查 Batch 脚本注释
            if (/Batch\s*脚本\s*\(\.bat\)/i.test(firstLine) || /\.bat\)/i.test(firstLine)) {
                console.log('[extractFileTypeFromCode] Detected Batch -> batch');
                return 'batch';
            }
            
            console.log('[extractFileTypeFromCode] No match, returning originalLang:', originalLang);
            return originalLang;
        }
        
        // 智能语言检测 - 优先使用启发式检测（更可靠）
        let displayLang = extractFileTypeFromCode(cleanedCode, language) || language;
        if (!displayLang || displayLang === '' || displayLang === 'plaintext') {
            // 首先使用启发式检测（对 HTML/CSS/JS 更准确）
            displayLang = quickLanguageDetect(cleanedCode);
            console.log('[marked.code] Heuristic detected lang:', displayLang);
            
            // 如果启发式检测返回空字符串，保持空字符串（不再尝试 hljs 自动检测）
            // 这样可以降低 plaintext 高亮触发频率
        } else {
            displayLang = language.toLowerCase();
        }
        
        // 保存原始显示标签
        const originalDisplayLang = displayLang;
        
        // 扩展的语言别名映射表 - 仅用于高亮，不影响显示标签
        const langAliases = {
            // JavaScript 家族
            'js': 'javascript', 'jsx': 'javascript', 'mjs': 'javascript', 'cjs': 'javascript',
            'ts': 'typescript', 'tsx': 'typescript',
            'coffee': 'coffeescript', 'litcoffee': 'coffeescript',
            
            // Python 家族
            'py': 'python', 'py3': 'python', 'python3': 'python',
            'pyi': 'python', 'pyw': 'python',
            
            // Shell 系列
            'sh': 'bash', 'shell': 'bash', 'zsh': 'bash', 'bash': 'bash',
            'fish': 'bash', 'ksh': 'bash',
            
            // C/C++ 系列
            'c++': 'cpp', 'cc': 'cpp', 'cxx': 'cpp', 'hpp': 'cpp',
            'h++': 'cpp', 'hxx': 'cpp', 'hh': 'cpp',
            'c': 'c', 'h': 'c',
            
            // C#/.NET 系列
            'c#': 'csharp', 'cs': 'csharp', 'csharp': 'csharp',
            'vb': 'vbnet', 'vbnet': 'vbnet',
            'fs': 'fsharp', 'f#': 'fsharp', 'fsharp': 'fsharp',
            
            // Java 系列
            'java': 'java', 'jsp': 'java',
            'kt': 'kotlin', 'kts': 'kotlin', 'kotlin': 'kotlin',
            'groovy': 'groovy', 'gvy': 'groovy', 'gy': 'groovy', 'gsh': 'groovy',
            'scala': 'scala', 'sc': 'scala',
            
            // Go/Rust 系列
            'go': 'go', 'golang': 'go',
            'rs': 'rust', 'rust': 'rust',
            'zig': 'zig',
            
            // Ruby 系列
            'rb': 'ruby', 'ruby': 'ruby', 'rake': 'ruby',
            'erb': 'erb', 'eruby': 'erb',
            
            // PHP 系列
            'php': 'php', 'php3': 'php', 'php4': 'php', 'php5': 'php', 'phtml': 'php',
            
            // Swift/Objective-C
            'swift': 'swift', 'swiftui': 'swift',
            'objc': 'objectivec', 'obj-c': 'objectivec', 'objective-c': 'objectivec',
            'm': 'objectivec', 'mm': 'objectivec',
            
            // 数据格式
            'json': 'json', 'json5': 'json', 'jsonc': 'json',
            'yml': 'yaml', 'yaml': 'yaml',
            'toml': 'ini', 'tml': 'ini',
            'xml': 'xml', 'xsl': 'xml', 'xslt': 'xml', 'xhtml': 'xml',
            'svg': 'xml',
            
            // 标记语言
            'md': 'markdown', 'markdown': 'markdown', 'mkd': 'markdown', 'mkdn': 'markdown',
            'rst': 'rest', 'rest': 'rest',
            'adoc': 'asciidoc', 'asciidoc': 'asciidoc',
            'tex': 'latex', 'latex': 'latex',
            'org': 'org',
            
            // 数据库
            'sql': 'sql', 'psql': 'sql', 'plsql': 'sql',
            'pgsql': 'pgsql', 'postgres': 'pgsql', 'postgresql': 'pgsql',
            'mysql': 'sql', 'sqlite': 'sql',
            'tsql': 'sql', 'plsql': 'sql',
            
            // Web 前端
            'html': 'html', 'htm': 'html', 'xhtml': 'html',
            'css': 'css', 'scss': 'scss', 'sass': 'scss', 'less': 'less',
            'styl': 'stylus', 'stylus': 'stylus',
            'vue': 'vue', 'svelte': 'svelte',
            'pug': 'pug', 'jade': 'pug',
            'haml': 'haml',
            'handlebars': 'handlebars', 'hbs': 'handlebars',
            'mustache': 'handlebars',
            'ejs': 'ejs',
            
            // 配置文件
            'ini': 'ini', 'conf': 'ini', 'cfg': 'ini',
            'properties': 'properties', 'props': 'properties',
            'env': 'bash', 'dotenv': 'bash',
            'gitignore': 'gitignore', 'gitignore': 'plaintext',
            'dockerignore': 'dockerfile',
            
            // 容器/DevOps
            'dockerfile': 'dockerfile', 'docker': 'dockerfile',
            'podman': 'dockerfile',
            'compose': 'yaml', 'docker-compose': 'yaml',
            'helm': 'yaml', 'k8s': 'yaml', 'kubernetes': 'yaml',
            'ansible': 'yaml', 'playbook': 'yaml',
            'terraform': 'hcl', 'tf': 'hcl', 'hcl': 'hcl',
            'vagrantfile': 'ruby',
            'jenkinsfile': 'groovy',
            
            // 构建工具
            'makefile': 'makefile', 'make': 'makefile', 'mk': 'makefile',
            'cmake': 'cmake', 'cmakelists': 'cmake',
            'gradle': 'gradle', 'gradlew': 'gradle',
            'maven': 'xml', 'pom': 'xml',
            'bazel': 'bazel', 'bzl': 'starlark', 'starlark': 'starlark',
            'just': 'just', 'justfile': 'just',
            
            // 科学计算
            'r': 'r', 'rscript': 'r',
            'matlab': 'matlab', 'm': 'matlab',
            'julia': 'julia', 'jl': 'julia',
            'mathematica': 'mathematica', 'nb': 'mathematica',
            'sas': 'sas',
            'stata': 'stata', 'do': 'stata',
            
            // 函数式语言
            'hs': 'haskell', 'haskell': 'haskell', 'lhs': 'haskell',
            'elm': 'elm',
            'purescript': 'purescript',
            'idris': 'idris',
            'agda': 'agda',
            'clj': 'clojure', 'clojure': 'clojure', 'cljs': 'clojure',
            'cljs': 'clojurescript', 'clojurescript': 'clojurescript',
            'lisp': 'lisp', 'lsp': 'lisp', 'scm': 'scheme', 'scheme': 'scheme',
            'racket': 'scheme',
            'erl': 'erlang', 'erlang': 'erlang',
            'ex': 'elixir', 'elixir': 'elixir', 'exs': 'elixir',
            'gleam': 'gleam',
            'fennel': 'fennel',
            'ocaml': 'ocaml', 'ml': 'ocaml', 'mli': 'ocaml',
            'reason': 'reason', 're': 'reason', 'rei': 'reason',
            'res': 'rescript', 'resi': 'rescript', 'rescript': 'rescript',
            
            // 系统编程
            'asm': 'x86asm', 'assembly': 'x86asm', 'nasm': 'x86asm',
            's': 'x86asm',
            'wasm': 'wasm', 'wat': 'wasm',
            'cr': 'crystal', 'crystal': 'crystal',
            'nim': 'nim', 'nimrod': 'nim',
            'd': 'd', 'di': 'd',
            'ada': 'ada', 'adb': 'ada', 'ads': 'ada',
            'fortran': 'fortran', 'f90': 'fortran', 'f95': 'fortran', 'f03': 'fortran',
            'cobol': 'cobol', 'cbl': 'cobol', 'cob': 'cobol',
            
            // 脚本语言
            'lua': 'lua', 'wlua': 'lua',
            'perl': 'perl', 'pl': 'perl', 'pm': 'perl', 'pod': 'perl',
            'raku': 'raku', 'perl6': 'raku',
            'tcl': 'tcl', 'tk': 'tcl',
            'awk': 'awk', 'gawk': 'awk', 'nawk': 'awk',
            'sed': 'sed',
            'ps1': 'powershell', 'powershell': 'powershell', 'psm1': 'powershell',
            'bat': 'dos', 'cmd': 'dos', 'batch': 'dos',
            'vbs': 'vbscript', 'vbscript': 'vbscript',
            'ahk': 'autohotkey', 'autohotkey': 'autohotkey',
            'applescript': 'applescript',
            
            // 数据处理
            'csv': 'plaintext', 'tsv': 'plaintext',
            'protobuf': 'protobuf', 'proto': 'protobuf',
            'thrift': 'thrift',
            'avro': 'avro', 'avsc': 'avro',
            'capnp': 'capnproto', 'capnproto': 'capnproto',
            'flatbuffers': 'flatbuffers', 'fbs': 'flatbuffers',
            
            // API/查询语言
            'graphql': 'graphql', 'gql': 'graphql',
            'cypher': 'cypher', 'cql': 'cypher',
            'sparql': 'sparql',
            'gremlin': 'groovy',
            'prisma': 'prisma',
            
            // 硬件描述
            'verilog': 'verilog', 'v': 'verilog',
            'vhdl': 'vhdl', 'vhd': 'vhdl',
            'systemverilog': 'verilog', 'sv': 'verilog',
            
            // 其他语言
            'dart': 'dart', 'flutter': 'dart',
            'solidity': 'solidity', 'sol': 'solidity',
            'move': 'move',
            'cairo': 'cairo',
            'motoko': 'motoko',
            'vyper': 'python',
            'ligo': 'pascal',
            
            // 编辑器配置
            'vim': 'vim', 'viml': 'vim', 'vimscript': 'vim',
            'elisp': 'lisp', 'el': 'lisp', 'emacs': 'lisp',
            
            // 测试
            'gherkin': 'gherkin', 'feature': 'gherkin',
            'robot': 'robotframework', 'robotframework': 'robotframework',
            
            // 其他格式
            'diff': 'diff', 'patch': 'diff',
            'log': 'log',
            'nginx': 'nginx', 'nginxconf': 'nginx',
            'apache': 'apache', 'apacheconf': 'apache', 'htaccess': 'apache',
            'caddy': 'caddy', 'caddyfile': 'caddy',
            'redis': 'redis', 'redis-cli': 'redis',
            'haproxy': 'haproxy',
            
            // DevOps/基础设施
            'systemd': 'ini', 'service': 'ini',
            'kubernetes': 'yaml', 'k8s': 'yaml', 'k3s': 'yaml',
            'ansible': 'yaml', 'playbook': 'yaml',
            'docker-compose': 'yaml', 'compose': 'yaml',
            'openapi': 'yaml', 'swagger': 'yaml',
            'helm': 'yaml', 'chart': 'yaml',
            'argocd': 'yaml',
            'puppet': 'puppet', 'pp': 'puppet',
            'chef': 'ruby',
            'salt': 'yaml', 'sls': 'yaml',
            'packer': 'hcl', 'pkr': 'hcl',
            'nomad': 'hcl',
            'consul': 'hcl',
            
            // CI/CD
            'github-actions': 'yaml', 'githubaction': 'yaml', 'gha': 'yaml',
            'gitlab-ci': 'yaml', 'gitlabci': 'yaml',
            'circleci': 'yaml',
            'travis': 'yaml', 'travis-ci': 'yaml',
            'azure-pipelines': 'yaml',
            'bitbucket-pipelines': 'yaml',
            'drone': 'yaml',
            'woodpecker': 'yaml',
            
            // 科学/学术
            'bibtex': 'bibtex', 'bib': 'bibtex',
            'texinfo': 'texinfo',
            'lyx': 'latex',
            
            // 硬件/嵌入式
            'armasm': 'armasm', 'arm': 'armasm',
            'avr': 'cpp',
            'arduino': 'cpp', 'ino': 'cpp',
            
            // 其他语言补充
            'pascal': 'pascal', 'pas': 'pascal', 'pp': 'pascal',
            'delphi': 'delphi', 'dfm': 'delphi',
            'smalltalk': 'smalltalk', 'st': 'smalltalk',
            'eiffel': 'eiffel', 'e': 'eiffel',
            'oberon': 'oberon',
            'modula': 'modula2', 'modula2': 'modula2',
            'actionscript': 'actionscript', 'as': 'actionscript',
            'haxe': 'haxe', 'hx': 'haxe',
            'processing': 'java',
            'arduino': 'cpp',
            
            // 查询语言补充
            'xquery': 'xquery', 'xq': 'xquery',
            'xpath': 'xpath',
            'xslt': 'xml', 'xsl': 'xml',
            'mql': 'cpp',
            
            // 配置/数据格式补充
            'hocon': 'hocon', 'conf': 'hocon',
            'dhall': 'haskell',
            'jsonnet': 'jsonnet',
            'cue': 'cue',
            'pkl': 'pkl',
            'rego': 'rego', 'opa': 'rego',
            
            // 模型相关
            'onnx': 'protobuf',
            'gguf': 'plaintext',
            'safetensors': 'plaintext',
            
            // 特殊
            'plaintext': 'plaintext', 'text': 'plaintext', 'txt': 'plaintext',
            'mermaid': 'mermaid',
            'dot': 'dot', 'graphviz': 'dot',
            'plantuml': 'plantuml',
            'qasm': 'qasm', 'openqasm': 'qasm',
            
            // 新增冷门语言
            'asn1': 'vbscript',  // ASN.1 语法类似 VB
            'wdl': 'yaml',  // Workflow Description Language
            'nextflow': 'groovy',
            'snakemake': 'python',
            'nix': 'nix',
            'meson': 'meson',
            'bazel': 'bazel', 'bzl': 'starlark', 'starlark': 'starlark',
            'just': 'just', 'justfile': 'just',
            'systemtap': 'c', 'stp': 'c',
            'dtrace': 'c',
            'ebpf': 'c',
            'ebuild': 'bash',
            'pkgbuild': 'bash',
            'spec': 'rpm', 'rpm': 'rpm',
            'pcmk': 'xml',  // Pacemaker
            'corosync': 'ini',
            'keepalived': 'ini',
            'ha': 'ini',
        };
        
        // 高亮语言映射（不影响显示标签）
        const hljsLang = langAliases[displayLang] || displayLang;
        
        // 高亮处理 - 支持未知语言的优雅降级
        let highlighted;
        try {
            // 检查语言是否被 hljs 支持
            const isLangSupported = hljs.getLanguage(hljsLang);
            
            if (hljsLang === 'diff') {
                highlighted = hljs.highlight(cleanedCode, { language: 'diff' }).value;
            } else if (hljsLang === 'plaintext' || !isLangSupported) {
                // 对于不支持的语言，尝试自动检测或使用 plaintext
                if (!isLangSupported && cleanedCode.length > 50) {
                    try {
                        const autoResult = hljs.highlightAuto(cleanedCode);
                        if (autoResult.relevance > 5) {
                            highlighted = autoResult.value;
                            displayLang = autoResult.language || displayLang;
                        } else {
                            highlighted = escapeHtml(cleanedCode);
                        }
                    } catch (autoErr) {
                        highlighted = escapeHtml(cleanedCode);
                    }
                } else {
                    highlighted = escapeHtml(cleanedCode);
                }
            } else {
                highlighted = hljs.highlight(cleanedCode, { language: hljsLang, ignoreIllegals: true }).value;
            }
        } catch (err) {
            console.warn('[hljs.highlight] Failed for language:', displayLang, err);
            highlighted = escapeHtml(cleanedCode);
            displayLang = 'plaintext';
        }
        
        const codeId = 'code-' + Math.random().toString(36).substr(2, 9);
        const escapedCodeForAttr = escapeHtml(cleanedCode).replace(/"/g, '&quot;');
        
        // 语言显示名称映射 - 将短代码转换为可读名称
        const langDisplayNames = {
            'ex': 'ELIXIR', 'exs': 'ELIXIR', 'elixir': 'ELIXIR',
            'js': 'JAVASCRIPT', 'javascript': 'JAVASCRIPT',
            'ts': 'TYPESCRIPT', 'typescript': 'TYPESCRIPT',
            'py': 'PYTHON', 'python': 'PYTHON',
            'rb': 'RUBY', 'ruby': 'RUBY',
            'go': 'GO', 'golang': 'GO',
            'rs': 'RUST', 'rust': 'RUST',
            'java': 'JAVA',
            'cpp': 'C++', 'c++': 'C++', 'cc': 'C++',
            'cs': 'C#', 'csharp': 'C#', 'c#': 'C#',
            'php': 'PHP',
            'swift': 'SWIFT',
            'kt': 'KOTLIN', 'kotlin': 'KOTLIN',
            'scala': 'SCALA',
            'hs': 'HASKELL', 'haskell': 'HASKELL',
            'clj': 'CLOJURE', 'clojure': 'CLOJURE',
            'erl': 'ERLANG', 'erlang': 'ERLANG',
            'lua': 'LUA',
            'pl': 'PERL', 'perl': 'PERL',
            'r': 'R',
            'sh': 'BASH', 'bash': 'BASH', 'shell': 'SHELL',
            'ps1': 'POWERSHELL', 'powershell': 'POWERSHELL',
            'sql': 'SQL',
            'html': 'HTML',
            'css': 'CSS',
            'json': 'JSON',
            'yaml': 'YAML', 'yml': 'YAML',
            'xml': 'XML',
            'md': 'MARKDOWN', 'markdown': 'MARKDOWN',
            'dockerfile': 'DOCKERFILE',
            'makefile': 'MAKEFILE',
            'nginx': 'NGINX',
            'vim': 'VIM',
            'asm': 'ASSEMBLY',
            'dart': 'DART',
            'el': 'EMACS', 'elisp': 'EMACS',
            'lisp': 'LISP',
            'scm': 'SCHEME', 'scheme': 'SCHEME',
            'pas': 'PASCAL', 'pascal': 'PASCAL',
            'fortran': 'FORTRAN',
            'cobol': 'COBOL',
            'ada': 'ADA',
            'd': 'D',
            'nim': 'NIM',
            'crystal': 'CRYSTAL',
            'ocaml': 'OCAML',
            'f#': 'F#', 'fsharp': 'F#', 'fs': 'F#',
            'jl': 'JULIA', 'julia': 'JULIA',
            'matlab': 'MATLAB',
            'groovy': 'GROOVY',
            'coffee': 'COFFEESCRIPT', 'coffeescript': 'COFFEESCRIPT',
            'objc': 'OBJECTIVE-C', 'objective-c': 'OBJECTIVE-C',
            'raku': 'RAKU', 'perl6': 'RAKU',
            'tcl': 'TCL',
            'awk': 'AWK',
            'sed': 'SED',
            'bat': 'BATCH', 'batch': 'BATCH', 'cmd': 'BATCH',
            'ps': 'POSTSCRIPT', 'postscript': 'POSTSCRIPT',
            'proto': 'PROTOBUF', 'protobuf': 'PROTOBUF',
            'graphql': 'GRAPHQL', 'gql': 'GRAPHQL',
            'regex': 'REGEX',
            'toml': 'TOML',
            'ini': 'INI',
            'cfg': 'CONFIG',
            'env': 'ENV',
            'gitignore': 'GITIGNORE',
            'diff': 'DIFF',
            'patch': 'PATCH',
            'lock': 'LOCK',
            'log': 'LOG',
            'map': 'MAP',
            'txt': 'TEXT', 'text': 'TEXT',
            'sol': 'SOLIDITY', 'solidity': 'SOLIDITY',
            'vy': 'VY', 'vyper': 'VYPER',
            'move': 'MOVE',
            'cairo': 'CAIRO',
            'noir': 'NOIR',
            'svelte': 'SVELTE',
            'vue': 'VUE',
            'astro': 'ASTRO',
            'elm': 'ELM',
            'purescript': 'PURESCRIPT',
            'reason': 'REASON',
            'rescript': 'RESCRIPT',
            'gleam': 'GLEAM',
            'zig': 'ZIG',
            'odin': 'ODIN',
            'vala': 'VALA',
            'haxe': 'HAXE',
            'pug': 'PUG',
            'haml': 'HAML',
            'slim': 'SLIM',
            'ejs': 'EJS',
            'hbs': 'HANDLEBARS', 'handlebars': 'HANDLEBARS',
            'mustache': 'MUSTACHE',
            'jinja': 'JINJA',
            'twig': 'TWIG',
            'liquid': 'LIQUID',
            'nunjucks': 'NUNJUCKS',
            'xslt': 'XSLT',
            'dtd': 'DTD',
            'rss': 'RSS',
            'atom': 'ATOM',
            'mathml': 'MATHML',
            'svg': 'SVG',
            'mermaid': 'MERMAID',
            'plantuml': 'PLANTUML',
            'dot': 'DOT', 'graphviz': 'DOT',
            'ascii': 'ASCII',
            'box': 'BOX',
            'tree': 'TREE',
            'table': 'TABLE',
            'csv': 'CSV',
            'tsv': 'TSV',
        };
        
        // 转换显示名称
        const displayLangName = langDisplayNames[originalDisplayLang.toLowerCase()] || originalDisplayLang.toUpperCase();
        
        // 语言标签 - 为空时不显示
        const langLabel = originalDisplayLang ? `<span class="code-language">${displayLangName}</span>` : '';
        
        // 返回带头部和复制按钮的代码块 HTML
        // 使用原始显示标签，而不是高亮映射后的标签
        return `<div class="code-block-wrapper"><div class="code-block-header">${langLabel}<div class="code-block-actions"><button class="copy-code-btn" data-action="copy-code" data-code="${escapedCodeForAttr}" type="button"><i class="fas fa-copy"></i><span>复制</span></button></div></div><div class="code-block-scroll-container"><pre><code class="hljs language-${hljsLang}" data-code-id="${codeId}">${highlighted}</code></pre></div></div>`;
    };
    
    // 应用配置 - marked v9 需要使用 marked.use() 来应用自定义 renderer
    marked.use({ renderer: renderer });
    
    markedRendererConfigured = true;
    console.log('[configureMarkedRenderer] Configuration applied successfully');
}

// 统一处理Markdown内容
function processMarkdown(content, options = {}) {
    console.log('[processMarkdown] Processing content, length:', content?.length || 0);

    // 先解析思考过程（在Markdown解析前）
    const parsed = parseThinking(content);
    
    // 预处理：升级嵌套代码围栏，防止内部 ``` 被 marked 误解析
    const preprocessed = preprocessCodeFences(parsed.text);

    // 管线：preprocess -> parse -> post-process -> sanitize
    const parsedHtml = renderMarkdownWithBlockCache(preprocessed, options.renderState);
    const postProcessedHtml = applyMarkdownPostProcessors(parsedHtml, parsed);
    return sanitizeMarkdownHtml(postProcessedHtml);
}

function renderStreamingFrame(streamingDiv, options = {}) {
    if (!streamingDiv) return null;

    const lifecycle = getStreamingLifecycleState(streamingDiv);
    if (lifecycle && lifecycle.phase === 'idle') {
        transitionStreamingLifecycle(streamingDiv, 'start');
    }

    const {
        icon = 'fa-robot',
        messageStyle = '',
        statusText = '',
        statusColor = '#8b5cf6',
        prefixHtml = '',
        markdownContent = ''
    } = options;

    const safeStatusText = statusText ? escapeHtml(statusText) : '';
    const statusHtml = safeStatusText
        ? `<div class="streaming-status" style="font-size: 14px; color: ${statusColor}; margin-bottom: 6px;">${safeStatusText}</div>`
        : '';

    streamingDiv.innerHTML = `<div class="avatar"><i class="fas ${icon}"></i></div><div class="message-content" style="${messageStyle}">${statusHtml}${prefixHtml}<div class="streaming-content"></div></div>`;

    const contentDiv = streamingDiv.querySelector('.streaming-content');
    renderStreamingMarkdownImmediate(contentDiv, markdownContent || '', streamingDiv);
    return contentDiv;
}

function renderAssistantSegmentHtml(content) {
    const parsed = parseThinking(content || '');
    let cleanedText = fixDoublePipeTable(parsed.text || '');
    cleanedText = ensureTableSpacing(cleanedText);
    let formattedContent = processMarkdown(cleanedText);
    return formattedContent.replace(/<table>/g, '<table class="markdown-table">');
}

function renderStoredAgenticHistoryHtml(content) {
    const text = String(content || '').trimEnd();
    if (!text) return '';
    let approvalAnchorIndex = 0;
    const lines = text.split('\n').map((line) => {
        const escapedLine = escapeHtml(line);
        if (line.includes('已生成待审批修改卡片')) {
            approvalAnchorIndex += 1;
            return `<div class="stored-agentic-history-line change-set-origin-line" data-change-set-origin-index="${approvalAnchorIndex}">${escapedLine}<span class="change-set-origin-anchor" data-change-set-origin-index="${approvalAnchorIndex}"></span></div>`;
        }
        return `<div class="stored-agentic-history-line">${escapedLine || '&nbsp;'}</div>`;
    }).join('');
    return `<div class="stored-agentic-history" style="white-space:pre-wrap;word-break:break-word;">${lines}</div>`;
}

function buildAssistantStructuredHtml(finalAnswer = {}, thinkingTrace = '', footerHtml = '') {
    const finalContent = finalAnswer && finalAnswer.content ? String(finalAnswer.content) : '';
    const finalSummary = finalAnswer && finalAnswer.summary ? String(finalAnswer.summary) : '';
    const finalDetails = finalAnswer && finalAnswer.details ? String(finalAnswer.details) : '';
    const briefFirst = Boolean(finalAnswer && finalAnswer.brief_first);
    const thinkingHtml = thinkingTrace
        ? `<div class="assistant-thinking-block" data-open><div class="assistant-thinking-summary" onclick="toggleThinkingBlock(this)">思考过程（原始 Transcript）<i class="fas fa-chevron-down toggle-icon"></i></div><div class="assistant-thinking-body">${renderAssistantSegmentHtml(thinkingTrace)}</div></div>`
        : '';
    let finalHtml = `<div class="assistant-final-full">${renderAssistantSegmentHtml(finalContent)}</div>`;
    if (briefFirst && finalSummary) {
        finalHtml = `<div class="assistant-final-summary"><div class="assistant-final-summary-label">TL;DR</div>${renderAssistantSegmentHtml(finalSummary)}</div>${finalDetails ? `<div class="assistant-final-details"><div class="assistant-final-details-label">展开细节</div>${renderAssistantSegmentHtml(finalDetails)}</div>` : ''}`;
    }
    return `${thinkingHtml}<section class="assistant-final-panel"><div class="assistant-final-header">最终回答</div>${finalHtml}</section>${footerHtml || ''}`;
}

function renderAssistantStructuredFrame(messageDiv, finalAnswer = {}, thinkingTrace = '', footerHtml = '') {
    if (!messageDiv) return;
    const messageContent = messageDiv.querySelector('.message-content');
    if (!messageContent) return;
    const transcriptMarkdown = typeof messageDiv._agenticTranscriptMarkdown === 'string' && messageDiv._agenticTranscriptMarkdown
        ? messageDiv._agenticTranscriptMarkdown
        : thinkingTrace;
    const finalAnswerMarkdown = finalAnswer && finalAnswer.content ? String(finalAnswer.content) : '';
    const baseHtml = messageDiv._resumeBaseHtml || '';
    const existingHost = messageContent.querySelector(':scope > .change-set-host');
    if (existingHost) {
        existingHost.remove();
    }
    messageContent.innerHTML = baseHtml + buildAssistantStructuredHtml(finalAnswer, transcriptMarkdown, footerHtml);
    if (existingHost) {
        messageContent.appendChild(existingHost);
    }
    if (transcriptMarkdown) {
        messageDiv._agenticTranscriptMarkdown = transcriptMarkdown;
        messageDiv._fullResponseMarkdown = transcriptMarkdown;
    }
    if (finalAnswerMarkdown) {
        messageDiv._finalAnswerMarkdown = finalAnswerMarkdown;
    }
    rerenderStoredChangeSetCards(messageDiv);
    autoHighlightDiff(messageDiv);
}

function bindMarkdownInteractionHandlers() {
    if (markdownInteractionsBound) return;

    document.addEventListener('click', (event) => {
        const target = event.target && event.target.closest ? event.target.closest('.copy-code-btn') : null;
        if (!target) return;

        event.preventDefault();
        copyCodeToClipboard(target);
    });

    markdownInteractionsBound = true;
}

// 鲁棒的代码块自动检测和格式化 - 已移除
// 后端返回的格式已经是正确的，不需要自动检测
function smartCodeDetection(text) {
    return text;
}

// 快速启发式语言检测（renderer 使用）
function quickLanguageDetect(code) {
    const lines = code.split('\n');
    
    // 特殊处理：shebang 必须在第一行检测
    const firstLine = lines[0]?.trim() || '';
    if (firstLine.startsWith('#!')) {
        if (/bash|sh|zsh|ksh/.test(firstLine)) return 'bash';
        if (/python|python3/.test(firstLine)) return 'python';
        if (/ruby/.test(firstLine)) return 'ruby';
        if (/perl/.test(firstLine)) return 'perl';
    }
    
    // 跳过注释行，找到第一个实际代码行
    let firstCodeLine = '';
    let codeLines = [];
    for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        const trimmed = line.trim();
        // 跳过空行和注释（但保留 shebang 和 section 标记）
        if (!trimmed) continue;
        if (trimmed.startsWith('//') || 
            trimmed.startsWith('/*') ||
            trimmed.startsWith('* ') ||
            trimmed.startsWith('-- ') ||
            (trimmed.startsWith('#') && !trimmed.startsWith('#!') && !trimmed.startsWith('#['))) {
            continue;
        }
        if (!firstCodeLine) {
            firstCodeLine = trimmed;
        }
        codeLines.push(trimmed);
    }
    
    const codeContent = codeLines.join('\n');
    
    // SQL 特征（优先检测，因为 -- 注释被跳过）
    if (/^SELECT\s+|^INSERT\s+|^UPDATE\s+|^DELETE\s+|^CREATE\s+/i.test(firstCodeLine) ||
        /\bFROM\s+\w+|\bWHERE\s+/i.test(codeContent)) {
        return 'sql';
    }
    // INI/CFG 特征
    if (/^\[\w+\]$/.test(firstCodeLine)) {
        return 'ini';
    }
    // TOML 特征（有引号的值）
    if (/^\[\w+\]$/.test(firstCodeLine) && /\w+\s*=\s*["']/.test(codeContent)) {
        return 'toml';
    }
    // TypeScript 特征
    if (/:\s*(string|number|boolean|any)\s*[=;\)]/.test(codeContent) || 
        /interface\s+\w+|type\s+\w+\s*=|:\s*React\.FC/.test(codeContent)) {
        return 'typescript';
    }
    // Python 特征
    if (/^def\s+\w+|^class\s+\w+|^import\s+\w+|^from\s+\w+/.test(firstCodeLine) ||
        /print\s*\(|if\s+__name__\s*==/.test(codeContent)) {
        return 'python';
    }
    // Java 特征
    if (/^public\s+class|^public\s+static\s+void|System\.out\.print/.test(codeContent) ||
        /^package\s+\w+/.test(firstCodeLine)) {
        return 'java';
    }
    // Go 特征
    if (/^package\s+\w+|^func\s+main\s*\(|fmt\.Print/.test(codeContent)) {
        return 'go';
    }
    // Rust 特征
    if (/^fn\s+main\s*\(|println!/.test(codeContent)) {
        return 'rust';
    }
    // C++ 特征（优先于 C）
    if (/#include\s*<iostream>|std::|cout\s*<</.test(codeContent)) {
        return 'cpp';
    }
    // C 特征
    if (/#include\s*<stdio\.h>|printf\s*\(|int\s+main\s*\(/.test(codeContent)) {
        return 'c';
    }
    // C/C++ Header
    if (/#ifndef|#define|#endif/.test(codeContent)) {
        return 'cpp';
    }
    // Ruby 特征
    if (/^require\s+['"]|^def\s+\w+|puts\s+['"]/.test(codeContent)) {
        return 'ruby';
    }
    // PHP 特征
    if (/<\?php|echo\s+['"]/.test(codeContent)) {
        return 'php';
    }
    // Batch 特征
    if (/@echo\s+off/i.test(firstCodeLine) || /\bpause\b/i.test(codeContent)) {
        return 'dos';
    }
    // YAML 特征
    if (/^\w+:\s+/.test(firstCodeLine) || /^\s{2,}-?\s*\w+:/.test(codeContent)) {
        return 'yaml';
    }
    // JSX 特征
    if (/<\w+[^>]*>.*<\/\w+>/.test(codeContent) && /import\s+React|export\s+default/.test(codeContent)) {
        return 'javascript';
    }
    // HTML 特征
    if (/^<!DOCTYPE|^<html|<head|<body|<div/i.test(firstCodeLine)) {
        return 'html';
    }
    // CSS 特征
    if (/^[\w.#][\w-]*\s*\{/.test(firstCodeLine) || 
        /font-family|background-color|margin|padding/i.test(codeContent)) {
        return 'css';
    }
    // JavaScript 特征
    if (/console\.|document\.|window\.|^const\s+|^let\s+|^var\s+|^function\s+/.test(firstCodeLine)) {
        return 'javascript';
    }
    // JSON 特征
    if (/^\{.*"[\w\-]+"\s*:/.test(firstCodeLine) || /^\[\s*\{/.test(firstCodeLine)) {
        return 'json';
    }
    // Markdown 特征
    if (/^#{1,6}\s+\S/.test(firstCodeLine) || /\*\*[^*]+\*\*/.test(codeContent)) {
        return 'markdown';
    }
    
    // 未检测到明确语言特征，返回空字符串（避免 plaintext 高亮触发）
    return '';
}

function getCopyAllMessageContent(messageDiv, fallbackContent, role) {
    if (role === 'assistant') {
        const fullResponseMarkdown = typeof messageDiv?._fullResponseMarkdown === 'string' && messageDiv._fullResponseMarkdown
            ? messageDiv._fullResponseMarkdown
            : '';
        if (fullResponseMarkdown) {
            return fullResponseMarkdown;
        }

        const transcriptMarkdown = typeof messageDiv?._agenticTranscriptMarkdown === 'string' && messageDiv._agenticTranscriptMarkdown
            ? messageDiv._agenticTranscriptMarkdown
            : '';
        const finalAnswerMarkdown = typeof messageDiv?._finalAnswerMarkdown === 'string' && messageDiv._finalAnswerMarkdown
            ? messageDiv._finalAnswerMarkdown
            : '';

        if (transcriptMarkdown && finalAnswerMarkdown) {
            const trimmedTranscript = transcriptMarkdown.trimEnd();
            const trimmedFinalAnswer = finalAnswerMarkdown.trim();
            if (trimmedFinalAnswer && trimmedTranscript.endsWith(trimmedFinalAnswer)) {
                return trimmedTranscript;
            }
            return `${trimmedTranscript}\n\n${finalAnswerMarkdown.trimStart()}`;
        }
        if (transcriptMarkdown) {
            return transcriptMarkdown;
        }
        if (finalAnswerMarkdown) {
            return finalAnswerMarkdown;
        }

        const idx = getMessageIndex(messageDiv);
        if (idx >= 0) {
            const historyMsg = messageHistory[idx];
            const historyFullResponse = historyMsg?.agentic_transcript || historyMsg?.full_response_markdown || '';
            if (typeof historyFullResponse === 'string' && historyFullResponse) {
                return historyFullResponse;
            }

            const historyTranscript = historyMsg?.thinking_trace || '';
            const historyFinalAnswer = historyMsg?.final_answer?.content || historyMsg?.content || '';
            if (historyTranscript && historyFinalAnswer) {
                const trimmedTranscript = String(historyTranscript).trimEnd();
                const trimmedFinalAnswer = String(historyFinalAnswer).trim();
                if (trimmedFinalAnswer && trimmedTranscript.endsWith(trimmedFinalAnswer)) {
                    return trimmedTranscript;
                }
                return `${trimmedTranscript}\n\n${String(historyFinalAnswer).trimStart()}`;
            }
            if (historyTranscript) return String(historyTranscript);
            if (historyFinalAnswer) return String(historyFinalAnswer);
        }

        const messageContent = messageDiv?.querySelector('.message-content');
        if (messageContent) {
            const clone = messageContent.cloneNode(true);
            clone.querySelectorAll('.message-actions, .message-bottom-toolbar, .response-telemetry, .quick-reply-bar, .change-set-host, .approval-pending-anchor, .copy-code-btn').forEach((el) => el.remove());
            const fallbackText = clone.innerText?.trim() || clone.textContent?.trim() || '';
            if (fallbackText) {
                return fallbackText;
            }
        }
    }

    if (typeof fallbackContent === 'string') {
        return fallbackContent;
    }
    return '';
}

// 辅助函数：HTML 转义
function escapeHtml(text) {
    if (!text) return '';
    return text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

// 复制代码块功能（使用 data-code 属性）
function copyCodeToClipboard(btn) {
    // 从 data-code 属性获取已转义的代码，需要反转义
    const escapedCode = btn.getAttribute('data-code') || '';
    // 反转义
    const code = escapedCode
        .replace(/&quot;/g, '"')
        .replace(/&lt;/g, '<')
        .replace(/&gt;/g, '>')
        .replace(/&amp;/g, '&')
        .replace(/&#039;/g, "'");
    
    if (!code) {
        console.warn('[copyCodeToClipboard] No code to copy');
        return;
    }
    
    navigator.clipboard.writeText(code).then(() => {
        btn.classList.add('copied');
        const span = btn.querySelector('span');
        const originalText = span ? span.textContent : '复制';
        if (span) span.textContent = '已复制';
        
        setTimeout(() => {
            btn.classList.remove('copied');
            if (span) span.textContent = originalText;
        }, 2000);
    }).catch(err => {
        console.error('复制失败:', err);
        const span = btn.querySelector('span');
        if (span) span.textContent = '失败';
        
        setTimeout(() => {
            if (span) span.textContent = '复制';
        }, 2000);
    });
}

// 复制代码块功能（旧版兼容）
function copyCodeBlock(btn) {
    copyCodeToClipboard(btn);
}

function addMessageToUI(role, content, isLatest = false, isStreaming = false, attachments = [], meta = {}) {
    const messagesDiv = document.getElementById('messages');
    
    const messageDiv = document.createElement('div');
    // 将 'assistant' 映射为 'bot'，确保样式正确应用
    const displayRole = role === 'assistant' ? 'bot' : role;
    messageDiv.className = `message ${displayRole}${isStreaming ? ' streaming' : ''}`;
    
    // 解析思考过程（在Markdown解析前）
    const parsed = parseThinking(content);
    
    // 修复双竖线表格格式，并确保表格前后有空行
    let cleanedText = fixDoublePipeTable(parsed.text);
    cleanedText = ensureTableSpacing(cleanedText);
    
    // 调试：检查是否包含表格
    if (cleanedText.includes('| 模块 |')) {
        console.log('[addMessageToUI] Table detected in content');
        console.log('[addMessageToUI] Content preview:', cleanedText.substring(0, 200));
    }
    
    // 使用新的 processMarkdown 函数处理 Markdown（包括代码块高亮和复制按钮）
    let formattedContent = processMarkdown(cleanedText);
    
    // 调试：检查是否生成了table标签
    if (formattedContent.includes('<table')) {
        console.log('[addMessageToUI] Table HTML generated');
    } else if (cleanedText.includes('| 模块 |')) {
        console.log('[addMessageToUI] WARNING: Table content present but no table HTML generated');
    }
    
    // 将思考标记转换为 HTML
    formattedContent = formatThinkingToHTML(formattedContent, parsed.thinkingBlocks);
    
    // 增强表格样式
    formattedContent = formattedContent.replace(/<table>/g, '<table class="markdown-table">');

    const structuredFinalAnswer = displayRole === 'bot' && meta && meta.finalAnswer && meta.finalAnswer.content
        ? meta.finalAnswer
        : null;
    const structuredThinkingTrace = displayRole === 'bot' && meta && typeof meta.thinkingTrace === 'string'
        ? meta.thinkingTrace
        : '';
    const structuredTranscriptMarkdown = displayRole === 'bot' && meta && typeof meta.agenticTranscriptMarkdown === 'string' && meta.agenticTranscriptMarkdown
        ? meta.agenticTranscriptMarkdown
        : '';
    const structuredPendingChangeSetIds = displayRole === 'bot' && Array.isArray(meta?.pendingChangeSetIds)
        ? meta.pendingChangeSetIds.filter((id) => typeof id === 'string' && id.trim())
        : [];
    const structuredChangeSets = displayRole === 'bot' && Array.isArray(meta?.changeSets)
        ? (structuredPendingChangeSetIds.length > 0
            ? meta.changeSets.filter((changeSet) => structuredPendingChangeSetIds.includes(changeSet?.id))
            : meta.changeSets)
        : [];
    const hasStoredAgenticHistory = displayRole === 'bot' && typeof content === 'string' && /🔄\s*Agentic Turn|🔧\s*[A-Za-z_]+|\[Summary:/.test(content);
    const structuredFinalAnswerText = structuredFinalAnswer && structuredFinalAnswer.content
        ? String(structuredFinalAnswer.content).trim()
        : '';
    let derivedTranscriptMarkdown = structuredTranscriptMarkdown;
    if (!derivedTranscriptMarkdown && structuredFinalAnswer && hasStoredAgenticHistory && structuredFinalAnswerText) {
        const cleanedTrimmed = cleanedText.trimEnd();
        if (cleanedTrimmed.endsWith(structuredFinalAnswerText)) {
            derivedTranscriptMarkdown = cleanedTrimmed.slice(0, cleanedTrimmed.length - structuredFinalAnswerText.length).trimEnd();
        }
    }
    if (!derivedTranscriptMarkdown && displayRole === 'bot') {
        const historyIdx = getMessageIndex(messageDiv);
        const historyMsg = historyIdx >= 0 ? messageHistory[historyIdx] : null;
        const savedTranscript = typeof historyMsg?.agentic_transcript === 'string' && historyMsg.agentic_transcript
            ? historyMsg.agentic_transcript
            : (typeof historyMsg?.full_response_markdown === 'string' && historyMsg.full_response_markdown ? historyMsg.full_response_markdown : '');
        if (savedTranscript) {
            derivedTranscriptMarkdown = savedTranscript;
        }
    }
    const assistantMessageHtml = structuredFinalAnswer
        ? buildAssistantStructuredHtml(structuredFinalAnswer, derivedTranscriptMarkdown || structuredThinkingTrace)
        : formattedContent;

    if (displayRole === 'bot' && (derivedTranscriptMarkdown || structuredTranscriptMarkdown)) {
        const transcriptMarkdown = derivedTranscriptMarkdown || structuredTranscriptMarkdown;
        messageDiv._agenticTranscriptMarkdown = transcriptMarkdown;
        messageDiv._fullResponseMarkdown = transcriptMarkdown;
    }
    if (displayRole === 'bot' && structuredFinalAnswerText) {
        messageDiv._finalAnswerMarkdown = structuredFinalAnswerText;
    } else if (displayRole === 'bot' && typeof content === 'string') {
        messageDiv._finalAnswerMarkdown = content;
    }

    if (displayRole === 'bot' && (hasPendingChangeSets(structuredChangeSets) || structuredPendingChangeSetIds.length > 0)) {
        messageDiv.dataset.approvalPending = 'true';
    }

    const metaElapsedMs = Number.isFinite(Number(meta?.elapsedMs))
        ? Number(meta.elapsedMs)
        : (Number.isFinite(Number(meta?.stats?.elapsed_ms)) ? Number(meta.stats.elapsed_ms) : null);
    if (Number.isFinite(metaElapsedMs) && metaElapsedMs !== null) {
        messageDiv.dataset.elapsedMs = String(metaElapsedMs);
    }
    if (meta && meta.stats && typeof meta.stats === 'object') {
        const stats = meta.stats;
        if (stats.model_display) messageDiv.dataset.modelDisplayName = String(stats.model_display);
        if (stats.model) messageDiv.dataset.modelName = String(stats.model);
        if (stats.phase) messageDiv.dataset.llamaPhase = String(stats.phase);
        if (Number.isFinite(Number(stats.prompt_tokens))) messageDiv.dataset.promptTokens = String(Number(stats.prompt_tokens));
        if (Number.isFinite(Number(stats.completion_tokens))) messageDiv.dataset.completionTokens = String(Number(stats.completion_tokens));
        if (Number.isFinite(Number(stats.total_tokens))) messageDiv.dataset.totalTokens = String(Number(stats.total_tokens));
        if (Number.isFinite(Number(stats.elapsed_ms))) messageDiv.dataset.llamaElapsedMs = String(Number(stats.elapsed_ms));
        if (Number.isFinite(Number(stats.tokens_per_second))) messageDiv.dataset.tokensPerSecond = String(Number(stats.tokens_per_second));
        if (Number.isFinite(Number(stats.context_length))) messageDiv.dataset.contextLength = String(Number(stats.context_length));
        if (Number.isFinite(Number(stats.output_limit))) messageDiv.dataset.outputLimit = String(Number(stats.output_limit));
    }

    // 如果是 AI 最新消息，添加完成标识和系统状态按钮（现在在 addMessageActions 的 bottomToolbar 中）
    // 这里不再需要单独添加 response-actions
    
    const attachmentHTML = attachments.length
        ? `<div class="attachment-scroll" data-scroll="message">
            <button class="attachment-scroll-btn" data-scroll-left="message" type="button">
                <i class="fas fa-chevron-left"></i>
            </button>
            <div class="message-attachments">
                ${attachments.map((attachment) => {
                    const url = attachment.url || attachment.dataUrl || attachment.previewUrl || '';
                    const isImage = isImagePreviewable(attachment.type || '', attachment.name || '');
                    const contentAttr = attachment.content ? ` data-attachment-content="${encodeAttachmentContent(attachment.content)}"` : '';
                    if (isImage && url) {
                        return `<div class="message-attachment-image" data-attachment-name="${attachment.name || 'image'}" data-attachment-type="${attachment.type || ''}" data-attachment-url="${url}" data-attachment-size="${attachment.size || ''}"${contentAttr}>
                            <img src="${url}" alt="${attachment.name || 'image'}" />
                            <div class="attachment-preview"><img src="${url}" alt="${attachment.name || 'image'}" /></div>
                        </div>`;
                    }
                    return `<div class="message-attachment-file" data-attachment-name="${attachment.name || '附件'}" data-attachment-type="${attachment.type || ''}" data-attachment-url="${url}" data-attachment-size="${attachment.size || ''}"${contentAttr}><i class="fas fa-file"></i><span>${attachment.name || '附件'}</span></div>`;
                }).join('')}
            </div>
            <button class="attachment-scroll-btn" data-scroll-right="message" type="button">
                <i class="fas fa-chevron-right"></i>
            </button>
        </div>`
        : '';

    // 移除硬编码的内联样式，让CSS处理响应式布局
    const messageStyle = '';
    const avatarStyle = '';
    messageDiv.innerHTML = `
        <div class="avatar" style="${avatarStyle}">
            <i class="fas fa-${displayRole === 'user' ? 'user' : 'robot'}"></i>
        </div>
        <div class="message-content" style="${messageStyle}">${attachmentHTML}${assistantMessageHtml}</div>
    `;
    
    messagesDiv.appendChild(messageDiv);
    if (structuredPendingChangeSetIds.length > 0) {
        setMessagePendingChangeSetIds(messageDiv, structuredPendingChangeSetIds);
    } else if (structuredChangeSets.length > 0) {
        setMessagePendingChangeSetIds(messageDiv, structuredChangeSets.map((changeSet) => changeSet?.id));
    }
    if (structuredChangeSets.length > 0) {
        structuredChangeSets.forEach((changeSet) => rememberChangeSetForMessage(messageDiv, changeSet));
        rerenderStoredChangeSetCards(messageDiv);
        requestAnimationFrame(() => {
            stabilizeChangeSetHost(messageDiv, {
                preferDefaultPosition: !messageDiv.querySelector('.change-set-origin-anchor')
            });
        });
    } else if (displayRole === 'bot') {
        ensureApprovalPendingAnchor(messageDiv);
    }
    
    // 自动检测并高亮 diff 格式内容（处理未被自定义 renderer 捕获的 diff）
    autoHighlightDiff(messageDiv);

    if (displayRole === 'bot') {
        const rawAssistantContent = structuredFinalAnswer?.content || content;
        if (!messageDiv._fullResponseMarkdown && typeof rawAssistantContent === 'string' && rawAssistantContent) {
            messageDiv._fullResponseMarkdown = rawAssistantContent;
        }
    }
    
    // 添加消息操作按钮
    addMessageActions(messageDiv, structuredFinalAnswer?.content || content, role, isLatest);
    if (displayRole === 'bot' && isMessageApprovalPending(messageDiv)) {
        hydratePendingApprovalCards(currentSession || currentChatId, messageDiv);
    }

    const messageScroll = messageDiv.querySelector('[data-scroll="message"]');
    setupAttachmentScroll(messageScroll);
    bindAttachmentPreview(messageDiv);
    
    // 滚动到底部
    scrollToBottom();
    
    // 异步更新系统状态按钮（根据系统扫描结果切换绿色/红色呼吸灯）
    if (isLatest && role === 'assistant' && !isStreaming) {
        setTimeout(() => updateFixButtonStatus(), 500);
    }
    
    return messageDiv;
}

// 自动检测并高亮 diff 格式内容
function autoHighlightDiff(messageDiv) {
    const contentDiv = messageDiv.querySelector('.message-content');
    if (!contentDiv) return;
    
    // 只处理已经被 marked 渲染的代码块
    contentDiv.querySelectorAll('pre code').forEach(codeBlock => {
        const text = codeBlock.textContent || '';
        const lines = text.split('\n');
        
        // 如果已经是 diff 语言，跳过
        if (codeBlock.className.includes('language-diff') || codeBlock.className.includes(' diff ')) {
            return;
        }
        
        // 检查是否是 diff 格式
        // 条件1: 包含 --- 文件名 和 +++ 文件名
        // 条件2: 包含 @@ -数字 +数字 @@
        let hasMinusHeader = false;
        let hasPlusHeader = false;
        let hasDiffMarker = false;
        
        for (const line of lines.slice(0, 30)) { // 检查前30行
            if (/^---\s+/.test(line)) hasMinusHeader = true;
            if (/^\+\+\+\s+/.test(line)) hasPlusHeader = true;
            if (/^@@\s+-\d+/.test(line)) hasDiffMarker = true;
            if (/^diff\s+--git/.test(line)) {
                hasMinusHeader = true;
                hasPlusHeader = true;
                hasDiffMarker = true;
                break;
            }
        }
        
        // 只要满足以下条件就认为是 diff：
        // 1. 同时有 --- 和 +++ 开头的行，或
        // 2. 有 diff --git 开头，或
        // 3. 有 @@ 标记且有一些 - 或 + 开头的行
        const isDiff = (hasMinusHeader && hasPlusHeader) || 
                       /^diff\s+--git/m.test(text) ||
                       (hasDiffMarker && (/^-\s*/m.test(text) || /^\+\s*/m.test(text)));
        
        if (isDiff) {
            // 添加 language-diff 类
            codeBlock.classList.add('language-diff');
            
            // 应用高亮
            try {
                hljs.highlightElement(codeBlock);
                console.log('[autoHighlightDiff] Successfully highlighted diff block');
            } catch (e) {
                console.warn('[autoHighlightDiff] Highlight failed:', e);
            }
        }
    });
}

function showTypingIndicator() {
    const messagesDiv = document.getElementById('messages');
    
    const typingDiv = document.createElement('div');
    typingDiv.className = 'message bot typing-indicator';
    typingDiv.id = 'typingIndicator';
    typingDiv.innerHTML = `
        <div class="avatar">
            <i class="fas fa-robot"></i>
        </div>
        <div class="message-content typing">
            <div class="typing-dot"></div>
            <div class="typing-dot"></div>
            <div class="typing-dot"></div>
        </div>
    `;
    
    messagesDiv.appendChild(typingDiv);
    scrollToBottom();
}

function hideTypingIndicator() {
    const indicator = document.getElementById('typingIndicator');
    if (indicator) {
        indicator.remove();
    }
}

function shouldAutoScroll(messagesDiv = document.getElementById('messages')) {
    if (!messagesDiv) return false;
    const threshold = 120;
    return messagesDiv.scrollTop + messagesDiv.clientHeight >= messagesDiv.scrollHeight - threshold;
}

function scrollToBottom() {
    const messagesDiv = document.getElementById('messages');
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

function updateSendButton() {
    const sendBtn = document.getElementById('sendBtn');
    const stopBtn = document.getElementById('stopBtn');
    if (!sendBtn) return;
    
    // 检查当前会话是否有流式状态
    const streamingState = localStorage.getItem(`streaming_${currentChatId}`);
    const hasStreaming = Boolean(streamingState);
    let shouldShowStop = false;
    if (hasStreaming) {
        try {
            const state = JSON.parse(streamingState);
            shouldShowStop = state.isStreaming === true;
        } catch (e) {
            console.warn('[updateSendButton] Invalid streaming state, treating as not streaming');
        }
    }
    // 优先使用 isProcessing，其次使用流式状态
    const isCurrentlyProcessing = isProcessing || shouldShowStop;
    
    // 新版：如果 stopBtn 存在，采用“元宝风格”两个按钮切换
    if (stopBtn) {
        if (isCurrentlyProcessing) {
            sendBtn.style.display = 'none';
            stopBtn.style.display = 'inline-flex';
        } else {
            sendBtn.style.display = 'inline-flex';
            stopBtn.style.display = 'none';
        }
        return;
    }

    // 兼容模式：如果 stopBtn 不存在（例如页面还在用旧版 index.html），退回到原来的“同一个按钮换图标/文字”
    const icon = sendBtn.querySelector('i');
    const label = sendBtn.querySelector('span');
    if (!sendBtn.dataset.defaultIcon) {
        sendBtn.dataset.defaultIcon = icon ? icon.className : 'fas fa-paper-plane';
    }
    if (!sendBtn.dataset.defaultLabel) {
        sendBtn.dataset.defaultLabel = label ? label.textContent : '发送';
    }
    if (isCurrentlyProcessing) {
        sendBtn.classList.add('stop');
        sendBtn.disabled = false;
        if (icon) {
            icon.className = 'fas fa-stop';
        }
        if (label) {
            label.textContent = '停止';
        }
    } else {
        sendBtn.classList.remove('stop');
        sendBtn.disabled = false;
        if (icon) {
            icon.className = sendBtn.dataset.defaultIcon;
        }
        if (label) {
            label.textContent = sendBtn.dataset.defaultLabel;
        }
    }
}

// ═══════════════════════════════════════════════════════════════
// P3: Slash Command Completion
// ═══════════════════════════════════════════════════════════════

const _slashState = {
    skills: null,       // cached skill list from /api/skills
    visible: false,     // menu open?
    activeIdx: 0,       // highlighted item index
    filtered: [],       // currently shown items
    fetchPromise: null,  // in-flight fetch dedup
};

async function _fetchSkills() {
    if (_slashState.skills) return _slashState.skills;
    if (_slashState.fetchPromise) return _slashState.fetchPromise;
    _slashState.fetchPromise = fetch('/api/skills')
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                _slashState.skills = data.skills;
                // Add built-in non-skill commands
                const builtins = [
                    { name: 'help', description: '显示帮助信息', aliases: ['h'], argument_hint: '[topic]' },
                    { name: 'skills', description: '列出所有可用技能', aliases: [], argument_hint: '' },
                ];
                for (const b of builtins) {
                    if (!_slashState.skills.find(s => s.name === b.name)) {
                        _slashState.skills.push(b);
                    }
                }
            }
            _slashState.fetchPromise = null;
            return _slashState.skills || [];
        })
        .catch(e => {
            console.warn('[P3] Failed to fetch skills:', e);
            _slashState.fetchPromise = null;
            return [];
        });
    return _slashState.fetchPromise;
}

function _slashFilter(query) {
    const skills = _slashState.skills || [];
    if (!query) return skills.slice(0, 15);
    const q = query.toLowerCase();
    return skills.filter(s =>
        s.name.toLowerCase().startsWith(q) ||
        (s.aliases || []).some(a => a.toLowerCase().startsWith(q)) ||
        s.description.toLowerCase().includes(q)
    ).slice(0, 12);
}

function _slashRender(items) {
    const menu = document.getElementById('slashMenu');
    if (!menu) return;
    if (!items.length) {
        menu.innerHTML = '<div class="slash-menu-empty">没有匹配的命令</div>';
        return;
    }
    let html = '<div class="slash-menu-header">命令</div>';
    items.forEach((s, i) => {
        const activeClass = i === _slashState.activeIdx ? ' active' : '';
        const hint = s.argument_hint ? `<span class="slash-item-hint">${_escHtml(s.argument_hint)}</span>` : '';
        html += `<div class="slash-item${activeClass}" data-idx="${i}" onmousedown="_slashSelect(${i})">` +
            `<span class="slash-item-name">/${_escHtml(s.name)}</span>` +
            hint +
            `<span class="slash-item-desc">${_escHtml(s.description)}</span>` +
            `</div>`;
    });
    menu.innerHTML = html;
}

function _escHtml(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

function _slashShow(items) {
    const menu = document.getElementById('slashMenu');
    if (!menu) return;
    _slashState.filtered = items;
    _slashState.activeIdx = 0;
    _slashState.visible = true;
    _slashRender(items);
    menu.classList.add('visible');
}

function _slashHide() {
    const menu = document.getElementById('slashMenu');
    if (menu) menu.classList.remove('visible');
    _slashState.visible = false;
    _slashState.filtered = [];
}

function _slashSelect(idx) {
    const item = _slashState.filtered[idx];
    if (!item) return;
    const input = document.getElementById('messageInput');
    if (!input) return;
    input.value = '/' + item.name + ' ';
    input.focus();
    _slashHide();
    // Move cursor to end
    const len = input.value.length;
    input.setSelectionRange(len, len);
}

function _slashScrollActive() {
    const menu = document.getElementById('slashMenu');
    if (!menu) return;
    const active = menu.querySelector('.slash-item.active');
    if (active) active.scrollIntoView({ block: 'nearest' });
}

async function _slashOnInput() {
    const input = document.getElementById('messageInput');
    if (!input) return;
    const val = input.value;
    // Only trigger when line starts with / and cursor is on the first line
    const cursorPos = input.selectionStart;
    const textBeforeCursor = val.substring(0, cursorPos);
    // Check if we're on the first line and it starts with /
    const firstNewline = val.indexOf('\n');
    const isFirstLine = firstNewline === -1 || cursorPos <= firstNewline;

    if (!isFirstLine || !val.startsWith('/')) {
        if (_slashState.visible) _slashHide();
        return;
    }

    // Extract the command query (text after / up to first space or end)
    const firstSpace = val.indexOf(' ');
    const query = firstSpace === -1 ? val.substring(1) : val.substring(1, firstSpace);

    // If there's a space, the command is already typed — hide menu
    if (firstSpace !== -1 && cursorPos > firstSpace) {
        if (_slashState.visible) _slashHide();
        return;
    }

    await _fetchSkills();
    const items = _slashFilter(query);
    if (items.length === 0 && !query) {
        _slashHide();
        return;
    }
    _slashShow(items);
}

// 输入处理
function handleKeyDown(event) {
    // P3: Slash menu keyboard navigation
    if (_slashState.visible) {
        const count = _slashState.filtered.length;
        if (event.key === 'ArrowDown') {
            event.preventDefault();
            _slashState.activeIdx = (_slashState.activeIdx + 1) % Math.max(count, 1);
            _slashRender(_slashState.filtered);
            _slashScrollActive();
            return;
        }
        if (event.key === 'ArrowUp') {
            event.preventDefault();
            _slashState.activeIdx = (_slashState.activeIdx - 1 + count) % Math.max(count, 1);
            _slashRender(_slashState.filtered);
            _slashScrollActive();
            return;
        }
        if (event.key === 'Enter' || event.key === 'Tab') {
            if (count > 0) {
                event.preventDefault();
                _slashSelect(_slashState.activeIdx);
                return;
            }
        }
        if (event.key === 'Escape') {
            event.preventDefault();
            _slashHide();
            return;
        }
    }

    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        handleSendOrStop();
    }
}

function autoResize(textarea) {
    // 保存当前滚动位置
    const scrollPos = window.scrollY;
    
    // 重置高度为auto以获取真实scrollHeight
    textarea.style.height = 'auto';
    
    // 计算新高度：至少84px(4行)，最多420px(20行)
    const minHeight = 84;  // 4行
    const maxHeight = 252; // 20行
    
    // 根据内容计算高度
    let newHeight = Math.max(textarea.scrollHeight, minHeight);
    newHeight = Math.min(newHeight, maxHeight);
    
    // 应用新高度
    textarea.style.height = newHeight + 'px';
    
    // 恢复滚动位置
    window.scrollTo(0, scrollPos);

    // P3: Trigger slash completion check
    _slashOnInput();
}

function clearInput() {
    const input = document.getElementById('messageInput');
    input.value = '';
    input.style.height = 'auto';
    input.focus();
    // P3: Hide slash menu on clear
    _slashHide();
}

// 对话管理
function startNewChat() {
    currentChatId = Date.now().toString();
    currentSession = currentChatId;
    clearCurrentTaskIdCache('startNewChat');
    resetTokenStatusBar();
    resetSidebarTaskProgress();
    document.getElementById('messages').innerHTML = `
        <div class="message bot">
            <div class="avatar">
                <i class="fas fa-robot"></i>
            </div>
            <div class="message-content">
                <p>👋 新对话开始了！有什么我可以帮你的吗？</p>
            </div>
        </div>
    `;
    messageHistory = [];
    document.getElementById('chatTitle').textContent = '新对话';
    loadChatHistory();
    renderTaskStatusBar({ root_task: {}, child_tasks: [], session_id: currentChatId }, { emptyMessage: '新对话已创建，等待任务开始。' });
    refreshTaskStatusBar(currentChatId, { force: true }).catch((error) => {
        console.warn('[startNewChat] Failed to refresh task status bar:', error);
    });
    syncFileChangeSummaryForActiveSession(currentChatId, {
        expanded: false,
    }).catch((error) => {
        console.warn('[startNewChat] Failed to refresh file change summary:', error);
    });
}

function clearChat() {
    if (confirm('确定要清空当前对话吗？')) {
        document.getElementById('messages').innerHTML = `
            <div class="message bot">
                <div class="avatar">
                    <i class="fas fa-robot"></i>
                </div>
                <div class="message-content">
                    <p>对话已清空。有什么我可以帮你的吗？</p>
                </div>
            </div>
        `;
        messageHistory = [];
        syncFileChangeSummaryForActiveSession(currentChatId, {
            expanded: false,
        }).catch((error) => {
            console.warn('[clearChat] Failed to refresh file change summary:', error);
        });
    }
}

// 导出对话 - 支持两种格式
function exportChat(format = 'json') {
    if (format === 'txt') {
        // 纯文本格式
        const chatContent = messageHistory.map(msg => {
            const role = msg.role === 'user' ? 'You' : 'Nanobot';
            return `${role}:\n${msg.content}\n\n---\n\n`;
        }).join('');
        
        const blob = new Blob([chatContent], { type: 'text/plain' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `nanobot-chat-${currentChatId}.txt`;
        a.click();
        URL.revokeObjectURL(url);
    } else {
        // JSON 格式（默认，包含完整数据）
        const chatData = {
            sessionId: currentChatId,
            timestamp: new Date().toISOString(),
            messages: messageHistory,
            config: typeof chatConfig !== 'undefined' ? chatConfig : null
        };
        
        const blob = new Blob([JSON.stringify(chatData, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `chat_${currentChatId}_${Date.now()}.json`;
        a.click();
        URL.revokeObjectURL(url);
        
        showNotification('对话已导出');
    }
}

// 历史记录管理（使用 localStorage）
function saveChatToHistory() {
    if (messageHistory.length === 0) return;
    
    const chatItem = {
        id: currentChatId,
        title: generateChatTitle(),
        timestamp: Date.now(),
        preview: messageHistory[0]?.content?.substring(0, 50) + '...'
    };
    const storedMeta = getChatMeta(currentChatId);
    if (storedMeta) {
        chatItem.pinned = storedMeta.pinned || false;
        chatItem.group = storedMeta.group || '未分组';
    }
    
    let history = JSON.parse(localStorage.getItem('nanobot_chats') || '[]');
    
    // 更新或添加
    const existingIndex = history.findIndex(h => h.id === currentChatId);
    if (existingIndex >= 0) {
        history[existingIndex] = chatItem;
    } else {
        // 去重：如果存在相同标题且对话内容只有1条用户消息的条目，合并
        const dupeIndex = history.findIndex(h =>
            h.id !== currentChatId &&
            h.title === chatItem.title &&
            Math.abs(h.timestamp - chatItem.timestamp) < 60000
        );
        if (dupeIndex >= 0) {
            const dupeId = history[dupeIndex].id;
            const dupeMessages = localStorage.getItem(`chat_${dupeId}`);
            const dupeLen = dupeMessages ? JSON.parse(dupeMessages).length : 0;
            // 如果旧条目只有<=2条消息（一轮对话），合并到当前条目
            if (dupeLen <= 2) {
                localStorage.removeItem(`chat_${dupeId}`);
                localStorage.removeItem(`streaming_${dupeId}`);
                history.splice(dupeIndex, 1);
                console.log(`[saveChatToHistory] Merged duplicate entry ${dupeId} into ${currentChatId}`);
            }
        }
        history.unshift(chatItem);
    }
    
    // 清理：移除指向空数据的条目
    history = history.filter(h => {
        if (h.id === currentChatId) return true;
        const data = localStorage.getItem(`chat_${h.id}`);
        if (!data || data === '[]') {
            localStorage.removeItem(`chat_${h.id}`);
            localStorage.removeItem(`streaming_${h.id}`);
            return false;
        }
        return true;
    });
    
    // 限制数量
    history = history.slice(0, CONFIG.MAX_HISTORY);
    
    localStorage.setItem('nanobot_chats', JSON.stringify(history));
    localStorage.setItem(`chat_${currentChatId}`, JSON.stringify(messageHistory));
    
    loadChatHistory();
}

// 启动时清理重复历史记录
function deduplicateHistory() {
    let history = JSON.parse(localStorage.getItem('nanobot_chats') || '[]');
    if (history.length <= 1) return;
    
    const seen = new Map(); // title → best entry (most messages)
    const toRemove = [];
    
    for (let i = 0; i < history.length; i++) {
        const h = history[i];
        const data = localStorage.getItem(`chat_${h.id}`);
        
        // 移除无数据的条目
        if (!data || data === '[]') {
            toRemove.push(i);
            localStorage.removeItem(`chat_${h.id}`);
            localStorage.removeItem(`streaming_${h.id}`);
            continue;
        }
        
        let msgCount = 0;
        try { msgCount = JSON.parse(data).length; } catch(e) { /* skip */ }
        
        const key = h.title || '';
        if (seen.has(key)) {
            const prev = seen.get(key);
            // 保留消息更多的条目，删除较少的
            if (msgCount > prev.msgCount) {
                // 当前更好，标记旧的删除
                toRemove.push(prev.index);
                localStorage.removeItem(`chat_${prev.id}`);
                localStorage.removeItem(`streaming_${prev.id}`);
                seen.set(key, { index: i, id: h.id, msgCount });
            } else {
                // 旧的更好，标记当前删除
                toRemove.push(i);
                localStorage.removeItem(`chat_${h.id}`);
                localStorage.removeItem(`streaming_${h.id}`);
            }
        } else {
            seen.set(key, { index: i, id: h.id, msgCount });
        }
    }
    
    if (toRemove.length > 0) {
        const removeSet = new Set(toRemove);
        history = history.filter((_, i) => !removeSet.has(i));
        localStorage.setItem('nanobot_chats', JSON.stringify(history));
        console.log(`[deduplicateHistory] Removed ${toRemove.length} duplicate/empty entries`);
    }
}

// 深度清理每条对话内部的重复消息 + 后端对账
async function deepCleanAllChatMessages() {
    const history = JSON.parse(localStorage.getItem('nanobot_chats') || '[]');
    let totalCleaned = 0;
    
    for (const h of history) {
        const raw = localStorage.getItem(`chat_${h.id}`);
        if (!raw) continue;
        try {
            const messages = JSON.parse(raw);
            if (!Array.isArray(messages)) continue;
            
            // Step 1: 本地去重
            let cleaned = normalizeChatHistory(messages);
            const beforeMergeSerialized = JSON.stringify(cleaned);
            
            // Step 2: 后端对账 — 如果本地消息比后端多出很多，用后端版本
            if (CONFIG.ENABLE_BACKEND_HISTORY && cleaned.length > 0) {
                try {
                    const resp = await fetch(`${CONFIG.API_URL}/api/sessions/${h.id}`);
                    if (resp.ok) {
                        const data = await resp.json();
                        const backendHistory = data.history;
                        if (Array.isArray(backendHistory) && backendHistory.length > 0) {
                            const mergedHistory = mergeHistoryPreferLocal(cleaned, backendHistory);
                            if (mergedHistory.length !== cleaned.length || JSON.stringify(mergedHistory) !== JSON.stringify(cleaned)) {
                                console.log(`[deepClean] chat_${h.id}: merged local history with backend history (${cleaned.length} local / ${backendHistory.length} backend)`);
                                cleaned = mergedHistory;
                            }
                        }
                    }
                } catch (e) { /* backend unavailable, skip */ }
            }
            
            const afterMergeSerialized = JSON.stringify(cleaned);
            if (cleaned.length < messages.length || afterMergeSerialized !== beforeMergeSerialized) {
                localStorage.setItem(`chat_${h.id}`, JSON.stringify(cleaned));
                const removed = messages.length - cleaned.length;
                totalCleaned += Math.max(0, removed);
                if (cleaned.length < messages.length) {
                    console.log(`[deepClean] chat_${h.id}: removed ${removed} duplicate messages (${messages.length} → ${cleaned.length})`);
                } else {
                    console.log(`[deepClean] chat_${h.id}: merged richer backend metadata into local history`);
                }
            }
        } catch (e) { /* skip unparseable */ }
    }
    if (totalCleaned > 0) {
        console.log(`[deepClean] Total: removed ${totalCleaned} duplicate messages across all chats`);
    }
}

function loadChatHistory() {
    const historyDiv = document.getElementById('chatHistory');
    const history = JSON.parse(localStorage.getItem('nanobot_chats') || '[]');
    
    if (history.length === 0) {
        historyDiv.innerHTML = '<div style="padding: 12px; color: var(--text-secondary); font-size: 13px;">暂无历史对话</div>';
        return;
    }
    
    const groups = normalizeHistoryGroups(history);
    const groupOrder = Object.keys(groups);
    historyDiv.innerHTML = groupOrder.map(groupName => {
        const items = groups[groupName];
        if (items.length === 0) return '';
        const header = `<div class="chat-group-header"><span class="group-dot"></span>${groupName}</div>`;
        const list = items.map(item => {
            const isActive = item.id === currentChatId;
            const isPinned = item.pinned;
            const isSelected = selectedChatIds.has(item.id);
            const checkbox = batchModeEnabled ? `
                <label class="chat-item-checkbox" onclick="event.stopPropagation()">
                    <input type="checkbox" data-chat-id="${item.id}" ${isSelected ? 'checked' : ''} onchange="toggleChatSelection('${item.id}')">
                </label>
            ` : '';
            const pinIcon = isPinned ? '<span class="chat-item-pin">📌</span>' : '';
            const actionButton = batchModeEnabled ? '' : `
                <button class="chat-item-action size-md" onclick="openChatItemMenu('${item.id}', event)">
                    <i class="fas fa-ellipsis"></i>
                </button>
            `;
            const clickHandler = batchModeEnabled
                ? `toggleChatSelection('${item.id}')`
                : `loadChat('${item.id}')`;
            const contextHandler = batchModeEnabled ? '' : `oncontextmenu="openChatItemMenu('${item.id}', event)"`;
            return `
                <div class="chat-item ${isActive ? 'active' : ''} ${batchModeEnabled ? 'batch' : ''}"
                    onclick="${clickHandler}"
                    ${contextHandler}>
                    ${checkbox}
                    <i class="fas fa-comment"></i>
                    <span class="chat-item-title">${item.title}</span>
                    ${pinIcon}
                    ${actionButton}
                </div>
            `;
        }).join('');
        return `${header}${list}`;
    }).join('');
    updateBatchToolbar();
}

function loadChat(chatId) {
    console.log('[loadChat] === START === chatId:', chatId);
    console.log('[loadChat] currentChatId before:', currentChatId);
    
    // 如果正在切换到不同对话，先停止当前轮询
    if (currentChatId !== chatId && streamingPollInterval) {
        clearInterval(streamingPollInterval);
        streamingPollInterval = null;
        console.log('[loadChat] Stopped polling for previous chat:', currentChatId);
    }
    
    // 确保聊天区域显示（隐藏homePage）
    console.log('[loadChat] Calling toggleHomePage(false)');
    toggleHomePage(false);
    
    // 验证toggleHomePage是否生效
    const homeEl = document.getElementById('homePage');
    const messagesEl = document.getElementById('messages');
    console.log('[loadChat] After toggleHomePage:');
    console.log('  - homePage display:', homeEl?.style.display, 'classList:', homeEl?.classList.contains('active'));
    console.log('  - messages display:', messagesEl?.style.display);
    
    let savedMessages = localStorage.getItem(`chat_${chatId}`);
    console.log('[loadChat] savedMessages exists:', !!savedMessages);
    
    // 如果localStorage没有数据，尝试从后端获取
    if (!savedMessages) {
        console.log('[loadChat] No local data, fetching from backend for:', chatId);
        // 同步获取后端数据
        fetch(`${CONFIG.API_URL}/api/sessions/${chatId}`)
            .then(response => response.json())
            .then(data => {
                if (data.history && data.history.length > 0) {
                    console.log('[loadChat] Got history from backend:', data.history.length, 'messages');
                    localStorage.setItem(`chat_${chatId}`, JSON.stringify(data.history));
                    // 重新调用loadChat
                    loadChat(chatId);
                } else {
                    console.log('[loadChat] No history found in backend');
                    // 显示空聊天提示
                    const messagesDiv = document.getElementById('messages');
                    messagesDiv.innerHTML = '<div class="message bot"><div class="message-content"><p>该对话没有历史记录</p></div></div>';
                }
            })
            .catch(err => {
                console.error('[loadChat] Failed to fetch from backend:', err);
                const messagesDiv = document.getElementById('messages');
                messagesDiv.innerHTML = '<div class="message bot"><div class="message-content"><p>加载历史记录失败</p></div></div>';
            });
        return;
    }
    
    if (savedMessages) {
        messageHistory = normalizeChatHistory(hydrateHistoryAttachments(JSON.parse(savedMessages)));
        currentChatId = chatId;
        currentSession = currentChatId;
        
        // 重建界面
        const messagesDiv = document.getElementById('messages');
        messagesDiv.innerHTML = '';
        
        // 强制显示messages区域（解决toggleHomePage后display:none的问题）
        messagesDiv.style.display = 'flex';
        messagesDiv.style.setProperty('display', 'flex', 'important');
        
        // 找到最后一条AI消息的索引
        let lastAssistantIndex = -1;
        for (let i = messageHistory.length - 1; i >= 0; i--) {
            if (messageHistory[i].role === 'assistant') {
                lastAssistantIndex = i;
                break;
            }
        }
        
        // 检查是否有未完成的流式响应需要恢复
        const streamingState = localStorage.getItem(`streaming_${chatId}`);
        const hasStreamingState = Boolean(streamingState);

        // 渲染消息，只有最后一条AI消息显示完成标识
        messageHistory.forEach((msg, index) => {
            const isStreaming = Boolean(msg.streaming);
            if (isStreaming && hasStreamingState) {
                return;
            }
            const isLatest = !isStreaming && (index === lastAssistantIndex);
            addMessageToUI(msg.role, msg.content, isLatest, isStreaming, msg.attachments || [], {
                elapsedMs: msg.elapsed_ms,
                stats: msg.llama_stats,
                finalAnswer: msg.final_answer,
                thinkingTrace: msg.thinking_trace || '',
                agenticTranscriptMarkdown: msg.agentic_transcript || msg.full_response_markdown || msg.thinking_trace || '',
                changeSets: msg.change_sets || [],
                pendingChangeSetIds: msg.pending_change_set_ids || []
            });
        });
        schedulePendingApprovalRestore(currentSession || currentChatId);
        
        // 检查是否有未完成的流式响应需要恢复（本地或后端）
        console.log('[loadChat] Checking streaming state for chat', chatId, ':', streamingState);
        if (streamingState) {
            try {
                const state = JSON.parse(streamingState);
                console.log('[loadChat] Parsed state:', state);
                
                if (state.isStreaming) {
                    const ageMs = Date.now() - (state.timestamp || 0);
                    if (ageMs > CONFIG.STREAMING_STALE_MS) {
                        console.log('[loadChat] Streaming state stale, clearing');
                        localStorage.removeItem(`streaming_${chatId}`);
                        // 更新按钮状态
                        updateSendButton();
                        return;
                    }
                    console.log('[loadChat] Restoring streaming message with content length:', state.content?.length || 0);
                    
                    // 确保流式消息元素不存在（避免重复）
                    const existingStreaming = messagesDiv.querySelector('#streamingMessage');
                    if (existingStreaming) {
                        existingStreaming.remove();
                    }
                    
                    // 恢复流式消息显示
                    const streamingDiv = document.createElement('div');
                    streamingDiv.className = 'message bot streaming';
                    streamingDiv.id = 'streamingMessage';

                    renderStreamingFrame(streamingDiv, {
                        icon: 'fa-robot',
                        markdownContent: state.content || '🤔 AI正在思考中...'
                    });
                    messagesDiv.appendChild(streamingDiv);
                    scrollToBottom();
                    console.log('[loadChat] Streaming message element added to DOM');
                    
                    // 启动轮询检查流式状态更新
                    console.log('[loadChat] Starting streaming poll for', chatId);
                    startStreamingPoll(chatId);
                } else {
                    console.log('[loadChat] State found but isStreaming is false or no content');
                    localStorage.removeItem(`streaming_${chatId}`);
                    // 更新按钮状态
                    updateSendButton();
                }
            } catch (e) {
                console.error('[loadChat] Error parsing streaming state:', e);
                localStorage.removeItem(`streaming_${chatId}`);
                // 更新按钮状态
                updateSendButton();
            }
        } else {
            console.log('[loadChat] No streaming state found for', chatId);
            resumeBackendStream(chatId, messagesDiv);
        }
        
        // 更新标题
        const history = JSON.parse(localStorage.getItem('nanobot_chats') || '[]');
        const chat = history.find(h => h.id === chatId);
        if (chat) {
            document.getElementById('chatTitle').textContent = chat.title;
        }
        
        loadChatHistory();
        // 切换会话后立即更新按钮状态
        updateSendButton();

        // 网关重启/恢复：加载会话时与后端对齐流式状态，清理残留 streaming_ 状态
        reconcileStreamingState(chatId).catch((e) => {
            console.warn('[loadChat] reconcileStreamingState failed:', e);
        });

        syncTaskStatusBarForActiveSession(chatId, { force: true }).catch((error) => {
            console.warn('[loadChat] Failed to refresh task status bar:', error);
        });
        syncFileChangeSummaryForActiveSession(currentSession || currentChatId, {
            expanded: fileChangeSummaryState.expanded,
        }).catch((error) => {
            console.warn('[loadChat] Failed to refresh file change summary:', error);
        });
    }
}

function generateChatTitle() {
    if (messageHistory.length === 0) return '新对话';
    
    const firstMessage = messageHistory[0].content;
    return firstMessage.substring(0, 20) + (firstMessage.length > 20 ? '...' : '');
}

// 检查连接状态（定期）
setInterval(checkConnection, 30000);

// ========== 消息操作功能 ==========

function formatLlamaElapsedTime(elapsedMs) {
    const totalSeconds = Math.max(0, Math.round(Number(elapsedMs) / 1000));
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    if (minutes > 0) {
        return `${minutes}min ${seconds}s`;
    }
    return `${seconds}s`;
}

function buildLlamaTelemetryRow(messageDiv) {
    if (!messageDiv) return '';

    const modelName = messageDiv.dataset.modelDisplayName || messageDiv.dataset.modelName || '';
    const phase = messageDiv.dataset.llamaPhase || 'Reading Generation';
    const completionTokens = Number(messageDiv.dataset.completionTokens || messageDiv.dataset.totalTokens || 0);
    const totalTokens = Number(messageDiv.dataset.totalTokens || completionTokens || 0);
    const elapsedMs = Number(messageDiv.dataset.llamaElapsedMs || messageDiv.dataset.elapsedMs || 0);
    const tokensPerSecond = Number(messageDiv.dataset.tokensPerSecond || 0);
    const contextLength = Number(messageDiv.dataset.contextLength || 0);
    const outputLimit = Number(messageDiv.dataset.outputLimit || 0);
    const promptTokens = Number(messageDiv.dataset.promptTokens || 0);

    if (!modelName || !Number.isFinite(completionTokens) || completionTokens <= 0 || !Number.isFinite(elapsedMs) || elapsedMs <= 0) {
        console.warn('[buildLlamaTelemetryRow] Skipped: modelName=', modelName, 'completionTokens=', completionTokens, 'elapsedMs=', elapsedMs);
        return '';
    }
    console.log('[buildLlamaTelemetryRow] Rendering: model=', modelName, 'tokens=', completionTokens, 'ms=', elapsedMs, 'tps=', tokensPerSecond);

    const tokenText = `${completionTokens.toLocaleString()} tokens`;
    const timeText = formatLlamaElapsedTime(elapsedMs);
    const speedText = `${tokensPerSecond > 0 ? tokensPerSecond.toFixed(2) : (completionTokens / Math.max(elapsedMs / 1000, 0.001)).toFixed(2)} t/s`;
    const contextPercent = Number.isFinite(contextLength) && contextLength > 0
        ? Math.min(100, (promptTokens / contextLength) * 100)
        : null;
    const outputPercent = Number.isFinite(outputLimit) && outputLimit > 0
        ? Math.min(100, (completionTokens / outputLimit) * 100)
        : null;
    const contextText = Number.isFinite(contextLength) && contextLength > 0
        ? `Context: ${Number(promptTokens).toLocaleString()}/${Number(contextLength).toLocaleString()} (${Math.round(contextPercent ?? 0)}%)`
        : `Context: ${Number(promptTokens).toLocaleString()}`;
    const outputText = Number.isFinite(outputLimit) && outputLimit > 0
        ? `Output: ${Number(completionTokens).toLocaleString()}/${Number(outputLimit).toLocaleString()} (${Math.round(outputPercent ?? 0)}%)`
        : `Output: ${Number(completionTokens).toLocaleString()}`;

    return `
        <div class="response-telemetry" title="llama.cpp 风格统计">
            <span class="telemetry-model">${escapeHtml(modelName)}</span>
            <span class="telemetry-phase">${escapeHtml(phase)}</span>
            <span class="telemetry-tokens">${tokenText}</span>
            <span class="telemetry-time">${timeText}</span>
            <span class="telemetry-speed">${speedText}</span>
            <span class="telemetry-context">${escapeHtml(contextText)}</span>
            <span class="telemetry-output">${escapeHtml(outputText)}</span>
        </div>
    `;
}

function addMessageActions(messageDiv, content, role, isLatest = false) {
    const actionsDiv = document.createElement('div');
    actionsDiv.className = 'message-actions';
    const approvalPending = isMessageApprovalPending(messageDiv);
    const resolveCopyContent = () => getCopyableMessageContent(messageDiv, content, role);
    const resolveCopyAllContent = () => getCopyAllMessageContent(messageDiv, content, role);

    // 复制全部按钮
    const copyAllBtn = document.createElement('button');
    copyAllBtn.className = 'msg-action-btn';
    copyAllBtn.innerHTML = '<i class="fas fa-copy"></i> 复制全部';
    copyAllBtn.onclick = () => copyMessage(copyAllBtn, resolveCopyAllContent());
    actionsDiv.appendChild(copyAllBtn);
    
    // 复制按钮
    const copyBtn = document.createElement('button');
    copyBtn.className = 'msg-action-btn';
    copyBtn.innerHTML = '<i class="fas fa-copy"></i> 复制';
    copyBtn.onclick = () => copyMessage(copyBtn, resolveCopyContent());
    actionsDiv.appendChild(copyBtn);
    
    // 编辑按钮（仅对用户消息显示）
    if (role === 'user') {
        const editBtn = document.createElement('button');
        editBtn.className = 'msg-action-btn';
        editBtn.innerHTML = '<i class="fas fa-edit"></i> 修改';
        editBtn.onclick = () => editMessage(messageDiv, content);
        actionsDiv.appendChild(editBtn);
    }
    
    messageDiv.appendChild(actionsDiv);
    
    // 添加底部工具栏
    const bottomToolbar = document.createElement('div');
    bottomToolbar.className = 'message-bottom-toolbar';
    const contentDiv = messageDiv.querySelector('.message-content');
    let telemetryRowEl = null; // telemetry 独立行元素（function scope）
    
    // 如果是最后一条 AI 消息，添加 telemetry 行 + 完成标识 + 系统状态
    if (role === 'assistant' && isLatest) {
        // telemetry 独立一行，稍后插在 bottomToolbar 之前（在 contentDiv 中）
        const telemetryHtml = approvalPending ? '' : buildLlamaTelemetryRow(messageDiv);
        if (telemetryHtml) {
            const telemetryWrapper = document.createElement('div');
            telemetryWrapper.innerHTML = telemetryHtml.trim();
            telemetryRowEl = telemetryWrapper.firstElementChild;
        }

        const elapsedMsFromMeta = Number(messageDiv?.dataset?.elapsedMs);
        const hasMetaElapsed = Number.isFinite(elapsedMsFromMeta) && elapsedMsFromMeta >= 0;
        const hasLiveElapsed = !hasMetaElapsed && processingStartTime !== null && isProcessing;
        const elapsedMs = hasMetaElapsed
            ? elapsedMsFromMeta
            : (hasLiveElapsed ? (Date.now() - processingStartTime) : null);
        const elapsedSec = Number.isFinite(elapsedMs) && elapsedMs > 0 ? (elapsedMs / 1000).toFixed(2) : null;
        // 完成标识（带耗时）— 如果等待审批则显示不同文案
        const completeDiv = document.createElement('div');
        completeDiv.className = 'response-complete';
        if (approvalPending) {
            completeDiv.innerHTML = '<i class="fas fa-hourglass-half"></i><span>请审批</span>';
        } else {
            completeDiv.innerHTML = '<i class="fas fa-check-circle"></i><span>回答已完成</span>' + (elapsedSec !== null ? '<span class="elapsed-time">⏱️ 耗时: ' + elapsedSec + 's</span>' : '');
        }
        bottomToolbar.appendChild(completeDiv);

        // 系统状态按钮
        if (!approvalPending) {
            const fixButtonDiv = document.createElement('div');
            fixButtonDiv.className = 'fix-bug-container status-normal';
            fixButtonDiv.setAttribute('onclick', 'openFixPanel()');
            fixButtonDiv.setAttribute('title', '打开系统状态面板');
            fixButtonDiv.innerHTML = '<i class="fas fa-shield-alt"></i><span>系统状态</span>';
            bottomToolbar.appendChild(fixButtonDiv);
        }
    }
    
    // SVG 图标
    const icons = {
        copy: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="14" height="14" x="8" y="8" rx="2" ry="2"></rect><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"></path></svg>`,
        edit: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.375 2.625a1 1 0 0 1 3 3l-9.013 9.014a2 2 0 0 1-.853.505l-2.873.84a.5.5 0 0 1-.62-.62l.84-2.873a2 2 0 0 1 .506-.852z"></path></svg>`,
        delete: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"></path><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"></path><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"></path><line x1="10" x2="10" y1="11" y2="17"></line><line x1="14" x2="14" y1="11" y2="17"></line></svg>`,
        chevronLeft: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 18-6-6 6-6"></path></svg>`,
        chevronRight: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 18 6-6-6-6"></path></svg>`
    };
    
    // 获取消息索引和版本信息
    const messageIndex = getMessageIndex(messageDiv);
    const messageData = messageIndex >= 0 ? messageHistory[messageIndex] : null;
    const versions = messageData?.versions || [];
    const currentVersion = messageData?.currentVersion ?? (versions.length > 0 ? versions.length - 1 : 0);
    const hasVersions = versions.length > 1;
    
    // 版本导航（仅当有多个版本时显示）
    if (hasVersions) {
        const versionNav = document.createElement('div');
        versionNav.className = 'message-version-nav';
        versionNav.setAttribute('role', 'navigation');
        versionNav.setAttribute('aria-label', `Message version ${currentVersion + 1} of ${versions.length}`);
        
        // 上一版本按钮
        const prevBtn = document.createElement('button');
        prevBtn.className = 'version-btn';
        prevBtn.innerHTML = icons.chevronLeft;
        prevBtn.title = '上一版本';
        prevBtn.setAttribute('aria-label', 'Previous message version');
        prevBtn.disabled = currentVersion <= 0;
        prevBtn.onclick = (e) => {
            e.stopPropagation();
            navigateMessageVersion(messageDiv, messageIndex, currentVersion - 1);
        };
        versionNav.appendChild(prevBtn);
        
        // 版本计数
        const versionCount = document.createElement('span');
        versionCount.className = 'version-count';
        versionCount.textContent = `${currentVersion + 1}/${versions.length}`;
        versionNav.appendChild(versionCount);
        
        // 下一版本按钮
        const nextBtn = document.createElement('button');
        nextBtn.className = 'version-btn';
        nextBtn.innerHTML = icons.chevronRight;
        nextBtn.title = '下一版本';
        nextBtn.setAttribute('aria-label', 'Next message version');
        nextBtn.disabled = currentVersion >= versions.length - 1;
        nextBtn.onclick = (e) => {
            e.stopPropagation();
            navigateMessageVersion(messageDiv, messageIndex, currentVersion + 1);
        };
        versionNav.appendChild(nextBtn);
        
        bottomToolbar.appendChild(versionNav);
    }
    
    // 复制按钮
    const copyAllToolbarBtn = document.createElement('button');
    copyAllToolbarBtn.className = 'toolbar-btn';
    copyAllToolbarBtn.innerHTML = icons.copy;
    copyAllToolbarBtn.title = '复制全部';
    copyAllToolbarBtn.setAttribute('aria-label', '复制全部');
    copyAllToolbarBtn.onclick = (e) => {
        e.stopPropagation();
        copyToolbarContent(copyAllToolbarBtn, resolveCopyAllContent());
    };
    bottomToolbar.appendChild(copyAllToolbarBtn);

    const copyToolbarBtn = document.createElement('button');
    copyToolbarBtn.className = 'toolbar-btn';
    copyToolbarBtn.innerHTML = icons.copy;
    copyToolbarBtn.title = '复制';
    copyToolbarBtn.setAttribute('aria-label', '复制');
    copyToolbarBtn.onclick = (e) => {
        e.stopPropagation();
        copyToolbarContent(copyToolbarBtn, resolveCopyContent());
    };
    bottomToolbar.appendChild(copyToolbarBtn);
    
    // 编辑按钮（仅对用户消息显示）
    if (role === 'user') {
        const editToolbarBtn = document.createElement('button');
        editToolbarBtn.className = 'toolbar-btn';
        editToolbarBtn.innerHTML = icons.edit;
        editToolbarBtn.title = '编辑';
        editToolbarBtn.setAttribute('aria-label', '编辑');
        editToolbarBtn.onclick = (e) => {
            e.stopPropagation();
            // 从消息历史获取实际内容
            const idx = getMessageIndex(messageDiv);
            const actualContent = idx >= 0 && messageHistory[idx] ? messageHistory[idx].content : content;
            editMessage(messageDiv, actualContent);
        };
        bottomToolbar.appendChild(editToolbarBtn);
    }
    
    // 分隔线
    const divider = document.createElement('div');
    divider.className = 'toolbar-divider';
    bottomToolbar.appendChild(divider);
    
    // 删除按钮
    const deleteToolbarBtn = document.createElement('button');
    deleteToolbarBtn.className = 'toolbar-btn';
    deleteToolbarBtn.innerHTML = icons.delete;
    deleteToolbarBtn.title = '删除';
    deleteToolbarBtn.setAttribute('aria-label', '删除');
    deleteToolbarBtn.onclick = (e) => {
        e.stopPropagation();
        deleteMessage(messageDiv);
    };
    bottomToolbar.appendChild(deleteToolbarBtn);
    
    // 将 telemetry 和工具栏添加到消息内容区域
    if (contentDiv) {
        if (telemetryRowEl) {
            contentDiv.appendChild(telemetryRowEl);
        }
        contentDiv.appendChild(bottomToolbar);
        if (role === 'assistant' && isLatest) {
            requestAnimationFrame(() => {
                scrollToBottom();
            });
        }
    }
}

function getCopyableMessageContent(messageDiv, fallbackContent, role) {
    if (role === 'assistant') {
        return getCopyAllMessageContent(messageDiv, fallbackContent, role);
    }

    const idx = getMessageIndex(messageDiv);
    if (idx >= 0) {
        const historyMsg = messageHistory[idx];
        const historyFullResponse = historyMsg?.agentic_transcript || historyMsg?.full_response_markdown || '';
        if (typeof historyFullResponse === 'string' && historyFullResponse) {
            return historyFullResponse;
        }
        const finalAnswer = historyMsg?.final_answer?.content;
        if (typeof finalAnswer === 'string' && finalAnswer) {
            return finalAnswer;
        }
        if (typeof historyMsg?.content === 'string' && historyMsg.content) {
            return historyMsg.content;
        }
    }

    if (role === 'assistant') {
        const messageContent = messageDiv?.querySelector('.message-content');
        const resumeBase = buildResumeBaseState(messageContent, messageDiv);
        if (resumeBase?.text) {
            return resumeBase.text;
        }
    }

    if (typeof fallbackContent === 'string') {
        return fallbackContent;
    }

    return '';
}

// 复制消息内容（底部工具栏版本）
function copyToolbarContent(btn, content) {
    navigator.clipboard.writeText(content).then(() => {
        btn.classList.add('copied');
        setTimeout(() => {
            btn.classList.remove('copied');
        }, 2000);
    }).catch(err => {
        console.error('复制失败:', err);
    });
}

// 删除消息
function deleteMessage(messageDiv) {
    // 确认删除
    if (!confirm('确定要删除这条消息吗？')) {
        return;
    }
    
    // 获取当前消息在消息历史中的索引
    const messageIndex = getMessageIndex(messageDiv);
    
    if (messageIndex >= 0 && messageIndex < messageHistory.length) {
        // 从历史记录中删除
        messageHistory.splice(messageIndex, 1);
        saveChatToHistory();
        
        // 从 UI 中删除
        messageDiv.style.transition = 'all 0.3s ease';
        messageDiv.style.opacity = '0';
        messageDiv.style.transform = 'translateX(-20px)';
        
        setTimeout(() => {
            messageDiv.remove();
            // 聊天列表会在 saveChatToHistory 中更新
        }, 300);
    }
}

// 获取消息在 DOM 中的索引（对应 messageHistory 的索引）
function getMessageIndex(messageDiv) {
    const messages = document.querySelectorAll('#messages .message');
    for (let i = 0; i < messages.length; i++) {
        if (messages[i] === messageDiv) {
            return i;
        }
    }
    return -1;
}

// 消息版本导航
function navigateMessageVersion(messageDiv, messageIndex, targetVersion) {
    if (messageIndex < 0 || messageIndex >= messageHistory.length) return;
    
    const messageData = messageHistory[messageIndex];
    if (!messageData.versions || targetVersion < 0 || targetVersion >= messageData.versions.length) return;
    
    // 更新当前版本
    messageData.currentVersion = targetVersion;
    const newContent = messageData.versions[targetVersion];
    messageData.content = newContent;
    
    // 保存到历史
    saveChatToHistory();
    
    // 更新 UI
    const contentDiv = messageDiv.querySelector('.message-content');
    if (contentDiv) {
        // 保留底部工具栏
        const toolbar = contentDiv.querySelector('.message-bottom-toolbar');
        const formattedContent = processMarkdown(newContent);
        contentDiv.innerHTML = formattedContent;
        if (toolbar) {
            contentDiv.appendChild(toolbar);
            // 更新版本导航显示
            const versionNav = toolbar.querySelector('.message-version-nav');
            if (versionNav) {
                const versionCount = versionNav.querySelector('.version-count');
                if (versionCount) {
                    versionCount.textContent = `${targetVersion + 1}/${messageData.versions.length}`;
                }
                const prevBtn = versionNav.querySelector('.version-btn:first-child');
                const nextBtn = versionNav.querySelector('.version-btn:last-child');
                if (prevBtn) prevBtn.disabled = targetVersion <= 0;
                if (nextBtn) nextBtn.disabled = targetVersion >= messageData.versions.length - 1;
            }
        }
    }
}

// 保存消息新版本（编辑后调用）
function saveMessageVersion(messageIndex, newContent) {
    if (messageIndex < 0 || messageIndex >= messageHistory.length) return;
    
    const messageData = messageHistory[messageIndex];
    if (!messageData.versions) {
        messageData.versions = [messageData.content];
        messageData.currentVersion = 0;
    }
    
    // 添加新版本
    messageData.versions.push(newContent);
    messageData.currentVersion = messageData.versions.length - 1;
    messageData.content = newContent;
    
    saveChatToHistory();
}

function copyMessage(btn, content) {
    navigator.clipboard.writeText(content).then(() => {
        btn.classList.add('copied');
        btn.innerHTML = '<i class="fas fa-check"></i> 已复制';
        setTimeout(() => {
            btn.classList.remove('copied');
            btn.innerHTML = '<i class="fas fa-copy"></i> 复制';
        }, 2000);
    }).catch(err => {
        console.error('复制失败:', err);
        btn.innerHTML = '<i class="fas fa-times"></i> 失败';
        setTimeout(() => {
            btn.innerHTML = '<i class="fas fa-copy"></i> 复制';
        }, 2000);
    });
}

function editMessage(messageDiv, originalContent) {
    console.log('[editMessage] 开始编辑消息, originalContent:', originalContent?.substring(0, 50) + '...');
    
    // 标记为编辑状态
    messageDiv.classList.add('editing');
    
    // 获取消息内容区域
    const contentDiv = messageDiv.querySelector('.message-content');
    const actionsDiv = messageDiv.querySelector('.message-actions');
    const bottomToolbar = messageDiv.querySelector('.message-bottom-toolbar');
    
    console.log('[editMessage] contentDiv:', contentDiv ? 'found' : 'not found');
    console.log('[editMessage] actionsDiv:', actionsDiv ? 'found' : 'not found');
    console.log('[editMessage] bottomToolbar:', bottomToolbar ? 'found' : 'not found');
    
    // 隐藏操作按钮（兼容两种工具栏）
    if (actionsDiv) actionsDiv.style.display = 'none';
    if (bottomToolbar) bottomToolbar.style.display = 'none';
    
    // 创建编辑区域
    const editArea = document.createElement('div');
    editArea.innerHTML = `
        <textarea class="edit-textarea" rows="auto">${originalContent}</textarea>
        <div class="edit-actions">
            <button class="edit-btn save"><i class="fas fa-check"></i> 保存</button>
            <button class="edit-btn cancel"><i class="fas fa-times"></i> 取消</button>
        </div>
    `;
    
    // 保存原始内容以便恢复
    contentDiv.dataset.originalContent = contentDiv.innerHTML;
    contentDiv.innerHTML = '';
    contentDiv.appendChild(editArea);
    
    // 聚焦到textarea并设置自适应高度
    const textarea = editArea.querySelector('.edit-textarea');
    textarea.style.minHeight = '80px';
    textarea.style.height = 'auto';
    
    // 根据内容计算高度
    const lines = originalContent.split('\n').length;
    const calculatedHeight = Math.max(80, Math.min(400, lines * 24 + 20));
    textarea.style.height = calculatedHeight + 'px';
    
    // 添加输入时自适应高度
    textarea.addEventListener('input', function() {
        this.style.height = 'auto';
        this.style.height = Math.max(80, Math.min(400, this.scrollHeight)) + 'px';
    });
    
    textarea.focus();
    textarea.setSelectionRange(textarea.value.length, textarea.value.length);
    
    // 保存按钮事件 - 仿照 DeepSeek/Windsurf 逻辑
    editArea.querySelector('.edit-btn.save').onclick = () => {
        const newContent = textarea.value.trim();
        console.log('[editMessage] 保存按钮被点击, newContent:', newContent?.substring(0, 50) + '...');
        console.log('[editMessage] newContent !== originalContent:', newContent !== originalContent);
        
        if (newContent && newContent !== originalContent) {
            // 获取当前消息在消息历史中的索引
            const messageIndex = getMessageIndex(messageDiv);
            console.log('[editMessage] messageIndex:', messageIndex, 'messageHistory.length:', messageHistory.length);
            
            if (messageIndex >= 0 && messageIndex < messageHistory.length) {
                // 保存为新版本（而不是直接覆盖）
                saveMessageVersion(messageIndex, newContent);
                
                // 删除该消息之后的所有消息（包括AI回复）
                const removedMessages = messageHistory.splice(messageIndex + 1);
                
                // 如果有后续消息被删除，需要清理 localStorage 中的相关数据
                removedMessages.forEach((msg, idx) => {
                    if (msg.role === 'assistant') {
                        localStorage.removeItem(`chat_response_${currentChatId}_${messageIndex + 1 + idx}`);
                    }
                });
                
                // 保存修改后的历史
                saveChatToHistory();
                
                // 重新渲染整个对话界面
                const messagesDiv = document.getElementById('messages');
                messagesDiv.innerHTML = '';
                
                // 找到最后一条AI消息索引用于显示完成标识
                let lastAssistantIndex = -1;
                for (let i = messageHistory.length - 1; i >= 0; i--) {
                    if (messageHistory[i].role === 'assistant') {
                        lastAssistantIndex = i;
                        break;
                    }
                }
                
                // 重新渲染所有消息
                messageHistory.forEach((msg, index) => {
                    const isLatest = (index === lastAssistantIndex);
                    addMessageToUI(msg.role, msg.content, isLatest, false, msg.attachments || [], {
                        elapsedMs: msg.elapsed_ms,
                        stats: msg.llama_stats,
                        finalAnswer: msg.final_answer,
                        thinkingTrace: msg.thinking_trace || '',
                        changeSets: msg.change_sets || [],
                        pendingChangeSetIds: msg.pending_change_set_ids || []
                    });
                });
                
                // 滚动到底部
                scrollToBottom();
                
                // 自动发送修改后的消息，触发AI重新生成回复（仿照 DeepSeek/Windsurf）
                // 使用 setTimeout 确保界面渲染完成后再发送
                console.log('[editMessage] 准备调用 resendEditedMessage...');
                setTimeout(() => {
                    console.log('[editMessage] 正在调用 resendEditedMessage...');
                    resendEditedMessage(newContent);
                }, 100);
            } else {
                console.error('[editMessage] messageIndex 无效:', messageIndex);
            }
        } else if (newContent === originalContent) {
            // 内容未改变，直接取消编辑
            console.log('[editMessage] 内容未改变，取消编辑');
            messageDiv.classList.remove('editing');
            if (contentDiv.dataset.originalContent) {
                contentDiv.innerHTML = contentDiv.dataset.originalContent;
            }
            if (actionsDiv) actionsDiv.style.display = 'flex';
            if (bottomToolbar) bottomToolbar.style.display = 'flex';
        } else {
            console.warn('[editMessage] newContent 为空或与原内容相同');
        }
    };
    
    // 取消按钮事件
    editArea.querySelector('.edit-btn.cancel').onclick = () => {
        console.log('[editMessage] 取消按钮被点击');
        messageDiv.classList.remove('editing');
        if (contentDiv.dataset.originalContent) {
            contentDiv.innerHTML = contentDiv.dataset.originalContent;
        }
        if (actionsDiv) actionsDiv.style.display = 'flex';
        if (bottomToolbar) bottomToolbar.style.display = 'flex';
    };
}

// 编辑后重新发送消息（不重复添加用户消息到历史）
async function resendEditedMessage(message) {
    const savedMessages = localStorage.getItem(`chat_${currentChatId}`);
    const historyForSession = savedMessages ? JSON.parse(savedMessages) : messageHistory;
    const payload = {
        message: message,
        session_id: currentSession || currentChatId,
        history: (historyForSession || []).slice(-CONFIG.MAX_HISTORY),
        attachments: [],
        model: selectedModel,
        backend: selectedBackend,
        mode: window._nanobotMode || 'code'
    };
    
    console.log('[resendEditedMessage] 重新发送编辑后的消息:', message.substring(0, 50) + '...');
    
    try {
        // 创建用于显示流式响应的消息元素
        const messagesDiv = document.getElementById('messages');
        const messageDiv = document.createElement('div');
        messageDiv.className = 'message bot streaming';
        messageDiv.id = 'streamingMessage';
        messageDiv.innerHTML = `
            <div class="avatar">
                <i class="fas fa-robot"></i>
            </div>
            <div class="message-content">
                <div class="streaming-content" style="white-space: pre-wrap; word-wrap: break-word;"></div>
            </div>
        `;
        messagesDiv.appendChild(messageDiv);
        scrollToBottom();
        
        const streamingContent = messageDiv.querySelector('.streaming-content');
        let fullResponse = '';
        let finalAnswerData = null;
        let thinkingTrace = '';
        let finalFooterHtml = '';
        let isStreaming = true;
        
        activeStreamingChatId = currentChatId;
        stopRequested = false;
        currentAbortController = new AbortController();
        
        // 使用 fetch 调用流式 API
        const response = await fetch(`${CONFIG.API_URL}/api/chat/stream`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(payload),
            signal: currentAbortController.signal
        });
        cacheCurrentTaskIdFromResponse(response, 'resendEditedMessage');
        
        // 检查是否是高危操作确认响应（非流式JSON响应）
        const contentType = response.headers.get('content-type') || '';
        if (contentType.includes('application/json') && !contentType.includes('text/event-stream')) {
            const jsonData = await response.json();
            if (jsonData.danger_check_required) {
                console.log('[resendEditedMessage] 检测到高危操作，显示确认弹窗');
                messageDiv.remove();
                
                if (typeof DangerModal !== 'undefined' && DangerModal.showApprovalRequest) {
                    DangerModal.showApprovalRequest({
                        request_id: 'danger_' + Date.now(),
                        danger_level: jsonData.danger_match.level,
                        category: jsonData.danger_match.category,
                        description: jsonData.danger_match.description,
                        suggestion: jsonData.danger_match.suggestion,
                        command: message,
                        timeout: 60
                    });
                } else {
                    alert(`高危操作警告: ${jsonData.danger_match.description}\n\n建议: ${jsonData.danger_match.suggestion}`);
                }
                
                hideTypingIndicator();
                isProcessing = false;
                updateSendButton();
                return;
            }
            
            throw new Error(jsonData.message || '未知错误');
        }
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let sseBuffer = ''; // SSE 行缓冲
        let responseStats = null;

        function handleResendSSELine(line) {
            if (!line.startsWith('data: ')) return;
            const data = JSON.parse(line.slice(6));

            if (data.type === 'start') {
                transitionStreamingLifecycle(messageDiv, 'start');
                streamingContent.textContent = '🤔 思考中...';
            } else if (data.type === 'generation_start') {
                messageDiv._generationStartPos = fullResponse.length;
            } else if (data.type === 'clear_generation') {
                if (typeof messageDiv._generationStartPos === 'number') {
                    fullResponse = fullResponse.substring(0, messageDiv._generationStartPos);
                    renderStreamingMarkdownImmediate(streamingContent, fullResponse, messageDiv);
                }
            } else if (data.type === 'final_answer') {
                finalAnswerData = {
                    content: data.content || '',
                    summary: data.summary || '',
                    details: data.details || '',
                    brief_first: Boolean(data.brief_first),
                    turn: data.turn || 0
                };
                const generationStartPos = typeof messageDiv._generationStartPos === 'number'
                    ? messageDiv._generationStartPos
                    : 0;
                thinkingTrace = fullResponse.substring(0, generationStartPos).trim();
                messageDiv._finalAnswerData = finalAnswerData;
                messageDiv._thinkingTrace = thinkingTrace;
                renderAssistantStructuredFrame(messageDiv, finalAnswerData, thinkingTrace, finalFooterHtml);
                if (shouldAutoScroll()) scrollToBottom();
            } else if (data.type === 'chunk') {
                transitionStreamingLifecycle(messageDiv, 'chunk');
                if (fullResponse === '' && data.content.includes('思考')) {
                    streamingContent.textContent = '';
                }
                fullResponse += data.content || '';
                const shouldStickToBottom = shouldAutoScroll();
                renderStreamingMarkdownImmediate(streamingContent, fullResponse, messageDiv);
                if (shouldStickToBottom) scrollToBottom();
            } else if (data.type === 'heartbeat') {
                transitionStreamingLifecycle(messageDiv, 'heartbeat');
                const shouldStickToBottom = shouldAutoScroll();
                renderStreamingMarkdownImmediate(
                    streamingContent,
                    buildHeartbeatContent(fullResponse, data),
                    messageDiv
                );
                if (shouldStickToBottom) scrollToBottom();
            } else if (data.type === 'done') {
                console.log('[resendEditedMessage] Done event received, stats:', data.stats ? 'present' : 'missing');
                transitionStreamingLifecycle(messageDiv, 'done');
                if (data.approval_pending) {
                    messageDiv.dataset.approvalPending = 'true';
                    setMessagePendingChangeSetIds(messageDiv, data.pending_change_set_ids || []);
                    console.log('[resendEditedMessage] Approval pending — change sets:', data.pending_change_set_ids);
                }
                if (!finalAnswerData && data.final_answer) {
                    finalAnswerData = data.final_answer;
                    if (typeof messageDiv._generationStartPos === 'number') {
                        thinkingTrace = fullResponse.substring(0, messageDiv._generationStartPos).trim();
                    } else if (finalAnswerData.content && fullResponse.includes(finalAnswerData.content)) {
                        const faIdx = fullResponse.lastIndexOf(finalAnswerData.content);
                        thinkingTrace = fullResponse.substring(0, faIdx).trim();
                    } else {
                        thinkingTrace = fullResponse.trim();
                    }
                }
                if (!fullResponse && data.response) {
                    fullResponse = data.response;
                }
                responseStats = data.stats || responseStats;
                // P1-1: Update token status bar (resend path)
                if (data.stats) updateTokenStatusBar(data.stats);
                // D2: Verification badge (resend path)
                const ds2 = data.stats || {};
                if (ds2.skill_mode) {
                    const vRan = ds2.verification_ran;
                    const nudges = ds2.criteria_nudges || 0;
                    const vClass = vRan ? 'd2-verify-pass' : 'd2-verify-skip';
                    const vIcon = vRan ? '🛡️' : '⚠️';
                    const vText = vRan ? '已验证' : '未验证';
                    let badge = `\n\n<span class="d2-verify-badge ${vClass}">${vIcon} ${vText}</span>`;
                    if (nudges > 0) badge += `<span class="d2-verify-badge d2-verify-skip">📋 ${nudges} 条标准提示</span>`;
                    let footerButtons = '';
                    // D6: Mode switch suggestion button (resend path)
                    const nextMode2 = ds2.suggested_next_mode;
                    if (nextMode2) {
                        const nextLabels2 = {refactor:'→ 切换到重构模式',verify:'→ 验证变更',debug:'→ 切换到调试模式'};
                        const nextLabel2 = nextLabels2[nextMode2] || `→ /${nextMode2}`;
                        footerButtons = `\n<button class="d6-mode-switch-btn" onclick="document.getElementById('userInput').value='/${nextMode2} ';document.getElementById('userInput').focus();">${nextLabel2}</button>\n`;
                    }
                    if (finalAnswerData) {
                        finalFooterHtml = `<div class="assistant-final-footer">${badge}${footerButtons}</div>`;
                        renderAssistantStructuredFrame(messageDiv, finalAnswerData, thinkingTrace, finalFooterHtml);
                    } else {
                        fullResponse += badge + '\n' + footerButtons;
                        renderStreamingMarkdownImmediate(streamingContent, fullResponse, messageDiv);
                    }
                }
                if (data.approval_pending || hasPendingChangeSets(messageDiv._changeSets || [])) {
                    syncPendingChangeSetsForSession(currentSession || currentChatId, messageDiv);
                }
            } else if (data.type === 'error_warning') {
                fullResponse += data.message;
                renderStreamingMarkdownImmediate(streamingContent, fullResponse, messageDiv);
                if (data.is_permission) {
                    console.warn('[PermissionError]', data.message);
                }
            } else if (data.type === 'agentic_turn') {
                fullResponse += `\n\n---\n🔄 **Agentic Turn ${data.turn || 0}**\n\n`;
                renderStreamingMarkdownImmediate(streamingContent, fullResponse, messageDiv);
            } else if (data.type === 'tool_start') {
                const tn = data.name || 'unknown';
                let ap = ''; try { const s = JSON.stringify(data.arguments||{}); ap = s.length>120?s.substring(0,120)+'...':s; } catch(e){ap='...';}
                fullResponse += `\n\n> 🔧 **${tn}** \`${ap}\`\n> ⏳ 执行中...\n`;
                renderStreamingMarkdownImmediate(streamingContent, fullResponse, messageDiv);
            } else if (data.type === 'tool_result') {
                const tn = data.name||'unknown', ok = data.success, ms = data.elapsed_ms||0;
                const isDT = tn === 'file_read' || tn === 'grep_search' || tn === 'file_edit';
                const mxC = isDT ? 2000 : 500, mxL = isDT ? 30 : 10;
                let out = (data.output||''); const totalLines = out.split('\n').length;
                out = out.length > mxC ? out.substring(0, mxC) + `\n... (${totalLines} lines total)` : out;
                const changeSet = normalizeIncomingChangeSet(data.change_set || null, tn);
                // P1-3: Use styled card instead of blockquote (resend path)
                if (changeSet) {
                    fullResponse += `\n> ✅ **${tn}** — 已生成待审批修改卡片。\n`;
                } else {
                    const isDiffR = tn === 'file_edit' || tn === 'file_write';
                    const cardOut = out.split('\n').slice(0, mxL).join('\n');
                    fullResponse += '\n\n' + buildToolResultCardHtml(tn, ok, ms, cardOut, isDiffR) + '\n\n';
                }
                renderStreamingMarkdownImmediate(streamingContent, fullResponse, messageDiv);
                if (changeSet) {
                    renderChangeSetCard(changeSet, messageDiv);
                    // Auto-accept/reject if session-level auto-action is set
                    if (getChangeSetAutoAction() === 'accept') {
                        console.log('[change-set] Auto-accepting (session policy, resend):', changeSet.id);
                        acceptChangeSet(changeSet.id, null);
                    } else if (getChangeSetAutoAction() === 'reject') {
                        console.log('[change-set] Auto-rejecting (session policy, resend):', changeSet.id);
                        rejectChangeSet(changeSet.id, null);
                    }
                } else if (tn === 'file_edit' || tn === 'file_write' || tn === 'change_set_accept' || tn === 'change_set_reject') {
                    syncPendingChangeSetsForSession(currentSession || currentChatId, messageDiv);
                }
            } else if (data.type === 'todo_update') {
                // P62: Todo list progress update (resend handler)
                const todos = data.todos || [];
                if (todos.length > 0) {
                    const completed = todos.filter(t => t.status === 'completed').length;
                    const total = todos.length;
                    const pct = Math.round((completed / total) * 100);
                    let todoMd = `\n\n> 📋 **任务进度** (${completed}/${total} — ${pct}%)\n`;
                    for (const t of todos) {
                        const icon = t.status === 'completed' ? '✅' : t.status === 'in_progress' ? '🔄' : '⬜';
                        todoMd += `> ${icon} ${t.content}\n`;
                    }
                    const todoPattern = /\n\n> 📋 \*\*任务进度\*\*[^]*?(?=\n\n[^>]|\n*$)/;
                    if (todoPattern.test(fullResponse)) {
                        fullResponse = fullResponse.replace(todoPattern, todoMd);
                    } else {
                        fullResponse += todoMd;
                    }
                    renderStreamingMarkdownImmediate(streamingContent, fullResponse, messageDiv);
                }
            } else if (data.type === 'task_update') {
                applyTaskUpdateEvent(data, currentSession || currentChatId);
            } else if (data.type === 'skill_mode') {
                // D2: Skill mode badge (resend path)
                const mode = data.mode || '';
                const modeLabels = {analyze:'分析模式',debug:'调试模式',verify:'验证模式',refactor:'重构模式'};
                const modeIcons = {analyze:'🔍',debug:'🐛',verify:'✅',refactor:'♻️'};
                const label = modeLabels[mode] || `/${mode}`;
                const icon = modeIcons[mode] || '⚙️';
                const colorClass = `d2-skill-${['analyze','debug','verify','refactor'].includes(mode) ? mode : 'default'}`;
                const badge = `<div class="d2-skill-badge ${colorClass}"><span class="d2-badge-dot"></span>${icon} ${label}</div>\n\n`;
                if (fullResponse.trim().length > 0) {
                    fullResponse = badge + fullResponse;
                } else {
                    fullResponse += badge;
                }
                renderStreamingMarkdownImmediate(streamingContent, fullResponse, messageDiv);
            } else if (data.type === 'sub_agent_start') {
                // D2: Sub-agent banner (resend path)
                const saType = data.agent_type || 'general';
                const saLabels = {explore:'探索 Agent',verify:'验证 Agent',plan:'规划 Agent',research:'研究 Agent',edit:'编辑 Agent'};
                const saLabel = saLabels[saType] || `${saType} Agent`;
                const taskPreview = data.task_preview ? `: ${data.task_preview}` : '';
                fullResponse += `\n\n<div class="d2-sub-agent-banner" id="d2-sa-${saType}-${data.turn||0}"><div class="d2-sa-spinner"></div><span>🤖 <b>${saLabel}</b> 正在分析${taskPreview}</span></div>\n`;
                renderStreamingMarkdownImmediate(streamingContent, fullResponse, messageDiv);
            } else if (data.type === 'sub_agent_end') {
                // D2: Sub-agent completion (resend path)
                const saType = data.agent_type || 'general';
                const ok = data.success;
                const elapsed = data.elapsed ? `${data.elapsed.toFixed(1)}s` : '';
                const bannerId = `d2-sa-${saType}-${data.turn||0}`;
                const runningPattern = new RegExp(`<div class="d2-sub-agent-banner" id="${bannerId}">[^]*?</div>`);
                const doneClass = ok ? 'd2-sa-done' : 'd2-sa-fail';
                const doneIcon = ok ? '✅' : '❌';
                const saLabels2 = {explore:'探索 Agent',verify:'验证 Agent',plan:'规划 Agent',research:'研究 Agent',edit:'编辑 Agent'};
                const saLabel2 = saLabels2[saType] || `${saType} Agent`;
                const replacement = `<div class="d2-sub-agent-banner ${doneClass}">${doneIcon} <b>${saLabel2}</b> ${ok?'完成':'失败'} ${elapsed ? `(${elapsed})` : ''}</div>`;
                if (runningPattern.test(fullResponse)) {
                    fullResponse = fullResponse.replace(runningPattern, replacement);
                } else {
                    fullResponse += `\n${replacement}\n`;
                }
                renderStreamingMarkdownImmediate(streamingContent, fullResponse, messageDiv);
            } else if (data.type === 'agentic_max_turns') {
                fullResponse += `\n\n---\n⚠️ *已达最大工具调用轮次 (${data.turns})*\n`;
                renderStreamingMarkdownImmediate(streamingContent, fullResponse, messageDiv);
            } else if (data.type === 'tool_execution') {
                // Legacy compat
            } else if (data.type === 'error') {
                transitionStreamingLifecycle(messageDiv, 'error');
                streamingContent.textContent = `❌ 错误: ${data.message}`;
                isStreaming = false;
                messageDiv.classList.remove('streaming');
            }
        }

        while (true) {
            const result = await reader.read();
            const { done, value } = result;
            if (done) {
                if (sseBuffer.trim()) {
                    try { handleResendSSELine(sseBuffer.trim()); } catch (e) {
                        console.warn('[resendEditedMessage] Final buffer parse failed, length:', sseBuffer.length);
                    }
                }
                break;
            }

            sseBuffer += decoder.decode(value, { stream: true });
            const lines = sseBuffer.split('\n');
            sseBuffer = lines.pop() || '';

            for (const line of lines) {
                try { handleResendSSELine(line); } catch (parseError) {
                    console.warn('[resendEditedMessage] SSE parse error, line length:', line.length);
                }
            }
        }

        // 流式完成后处理
        isStreaming = false;
        messageDiv.classList.remove('streaming');
        messageDiv.removeAttribute('id');

        let finalContent = fullResponse.trim();
        if (finalAnswerData && typeof finalAnswerData.content === 'string' && finalAnswerData.content.trim()) {
            finalContent = finalAnswerData.content.trim();
        }
        if (finalContent || hasPendingChangeSets(messageDiv._changeSets || []) || messageDiv.dataset.approvalPending === 'true') {
            const transcriptMarkdown = typeof fullResponse === 'string' ? fullResponse.trim() : '';
            if (transcriptMarkdown) {
                messageDiv._agenticTranscriptMarkdown = transcriptMarkdown;
                messageDiv._fullResponseMarkdown = transcriptMarkdown;
            }
            if (finalAnswerData && typeof finalAnswerData.content === 'string' && finalAnswerData.content.trim()) {
                messageDiv._finalAnswerMarkdown = finalAnswerData.content.trim();
            }
            const assistantEntry = {
                role: 'assistant',
                content: finalContent,
                agentic_transcript: transcriptMarkdown,
                full_response_markdown: transcriptMarkdown,
                final_answer: finalAnswerData,
                thinking_trace: thinkingTrace || '',
                change_sets: cloneChangeSetList(messageDiv._changeSets || [])
            };
            if (messageDiv.dataset.approvalPending === 'true' || hasPendingChangeSets(assistantEntry.change_sets || [])) {
                assistantEntry.approval_pending = true;
            }
            if (assistantEntry.change_sets.length > 0) {
                assistantEntry.pending_change_set_ids = assistantEntry.change_sets
                    .map((changeSet) => changeSet?.id)
                    .filter((id) => typeof id === 'string' && id.trim());
            }
            if (responseStats) {
                assistantEntry.elapsed_ms = Number.isFinite(Number(responseStats.elapsed_ms)) ? Number(responseStats.elapsed_ms) : undefined;
                assistantEntry.llama_stats = responseStats;
            }
            messageHistory.push(assistantEntry);
            saveChatToHistory();

            const contentDiv = messageDiv.querySelector('.message-content');
            if (contentDiv) {
                resetMarkdownBlockCacheState(messageDiv);
                if (finalAnswerData) {
                    renderAssistantStructuredFrame(messageDiv, finalAnswerData, thinkingTrace, finalFooterHtml);
                } else {
                    renderStreamingMarkdownImmediate(streamingContent, fullResponse.trim(), messageDiv);
                }
            }

            // 写入 telemetry dataset
            if (responseStats) {
                if (responseStats.model_display) messageDiv.dataset.modelDisplayName = String(responseStats.model_display);
                if (responseStats.model) messageDiv.dataset.modelName = String(responseStats.model);
                if (responseStats.phase) messageDiv.dataset.llamaPhase = String(responseStats.phase);
                if (Number.isFinite(Number(responseStats.prompt_tokens))) messageDiv.dataset.promptTokens = String(Number(responseStats.prompt_tokens));
                if (Number.isFinite(Number(responseStats.completion_tokens))) messageDiv.dataset.completionTokens = String(Number(responseStats.completion_tokens));
                if (Number.isFinite(Number(responseStats.total_tokens))) messageDiv.dataset.totalTokens = String(Number(responseStats.total_tokens));
                if (Number.isFinite(Number(responseStats.context_length))) messageDiv.dataset.contextLength = String(Number(responseStats.context_length));
                if (Number.isFinite(Number(responseStats.output_limit))) messageDiv.dataset.outputLimit = String(Number(responseStats.output_limit));
                if (Number.isFinite(Number(responseStats.elapsed_ms))) messageDiv.dataset.llamaElapsedMs = String(Number(responseStats.elapsed_ms));
                if (Number.isFinite(Number(responseStats.tokens_per_second))) messageDiv.dataset.tokensPerSecond = String(Number(responseStats.tokens_per_second));
            }
            const resendElapsedMs = responseStats && Number.isFinite(Number(responseStats.elapsed_ms))
                ? Number(responseStats.elapsed_ms)
                : (processingStartTime ? (Date.now() - processingStartTime) : null);
            if (Number.isFinite(resendElapsedMs) && resendElapsedMs >= 0) {
                messageDiv.dataset.elapsedMs = String(resendElapsedMs);
            }

            addMessageActions(messageDiv, finalContent, 'assistant', true);
            if (messageDiv.dataset.approvalPending === 'true' || hasPendingChangeSets(messageDiv._changeSets || [])) {
                hydratePendingApprovalCards(currentSession || currentChatId, messageDiv);
            }
            autoHighlightDiff(messageDiv);
        }

        scrollToBottom();
        
        // 如果流正常结束但没有收到 done 事件
        if (isStreaming) {
            isStreaming = false;
            messageDiv.classList.remove('streaming');
            messageDiv.removeAttribute('id');
            
            if (fullResponse.trim()) {
                const transcriptMarkdown = fullResponse.trim();
                messageDiv._agenticTranscriptMarkdown = transcriptMarkdown;
                messageDiv._fullResponseMarkdown = transcriptMarkdown;
                messageHistory.push({
                    role: 'assistant',
                    content: transcriptMarkdown,
                    agentic_transcript: transcriptMarkdown,
                    full_response_markdown: transcriptMarkdown,
                    change_sets: cloneChangeSetList(messageDiv._changeSets || []),
                    approval_pending: messageDiv.dataset.approvalPending === 'true'
                });
                saveChatToHistory();

                if (streamingContent) {
                    resetMarkdownBlockCacheState(messageDiv);
                    renderStreamingMarkdownImmediate(streamingContent, fullResponse.trim(), messageDiv);
                }
                
                addMessageActions(messageDiv, fullResponse.trim(), 'assistant', true);
                if (messageDiv.dataset.approvalPending === 'true' || hasPendingChangeSets(messageDiv._changeSets || [])) {
                    hydratePendingApprovalCards(currentSession || currentChatId, messageDiv);
                }
                autoHighlightDiff(messageDiv);
            }
            
            // 聊天列表会在 saveChatToHistory 中更新
            scrollToBottom();
        }
        
    } catch (error) {
        if (error.name === 'AbortError') {
            console.log('[resendEditedMessage] 请求被用户中止');
        } else {
            console.error('[resendEditedMessage] 发送失败:', error);
            const streamingMsg = document.getElementById('streamingMessage');
            if (streamingMsg) {
                const content = streamingMsg.querySelector('.streaming-content');
                if (content) content.textContent = `❌ 发送失败: ${error.message}`;
                streamingMsg.classList.remove('streaming');
            }
        }
    }
}

// 折叠/展开思考过程
function toggleThinking(label) {
    const block = label.parentElement;
    block.classList.toggle('collapsed');
    block.classList.toggle('expanded');
}

// 折叠/展开 thinking block (div-based, not details)
function toggleThinkingBlock(summary) {
    const block = summary.parentElement;
    const body = block.querySelector('.assistant-thinking-body');
    if (!body) return;
    const isOpen = block.hasAttribute('data-open');
    if (isOpen) {
        block.removeAttribute('data-open');
        body.style.display = 'none';
        summary.querySelector('.toggle-icon').style.transform = 'rotate(-90deg)';
    } else {
        block.setAttribute('data-open', '');
        body.style.display = '';
        summary.querySelector('.toggle-icon').style.transform = '';
    }
}

// ========== 全局固定工具栏功能 ==========

let currentVisibleMessage = null;

// 跟踪当前可见的消息
function trackVisibleMessage() {
    const messages = document.querySelectorAll('.message');
    const viewportCenter = window.innerHeight / 2;
    let closestMessage = null;
    let closestDistance = Infinity;
    
    messages.forEach(msg => {
        const rect = msg.getBoundingClientRect();
        const msgCenter = rect.top + rect.height / 2;
        const distance = Math.abs(msgCenter - viewportCenter);
        
        if (distance < closestDistance && rect.top < window.innerHeight && rect.bottom > 0) {
            closestDistance = distance;
            closestMessage = msg;
        }
    });
    
    if (closestMessage && closestMessage !== currentVisibleMessage) {
        currentVisibleMessage = closestMessage;
        updateGlobalToolbar();
    }
}

// 更新全局工具栏状态
function updateGlobalToolbar() {
    const toolbar = document.getElementById('globalToolbar');
    const editBtn = document.getElementById('globalEditBtn');
    
    if (!currentVisibleMessage) {
        toolbar.style.opacity = '0.5';
        return;
    }
    
    toolbar.style.opacity = '1';
    
    // 检查是否是用户消息
    const isUserMessage = currentVisibleMessage.classList.contains('user');
    if (editBtn) {
        editBtn.style.display = isUserMessage ? 'flex' : 'none';
    }
}

// 复制当前可见消息
function copyCurrentMessage() {
    if (!currentVisibleMessage) return;
    
    const messages = Array.from(document.querySelectorAll('.message'));
    const index = messages.indexOf(currentVisibleMessage);
    
    if (index >= 0 && index < messageHistory.length) {
        const content = messageHistory[index].content;
        navigator.clipboard.writeText(content).then(() => {
            const btn = document.getElementById('globalCopyBtn');
            btn.classList.add('copied');
            btn.innerHTML = '<i class="fas fa-check"></i> 已复制';
            setTimeout(() => {
                btn.classList.remove('copied');
                btn.innerHTML = '<i class="fas fa-copy"></i> 复制';
            }, 2000);
        });
    }
}

// 编辑当前可见消息
function editCurrentMessage() {
    if (!currentVisibleMessage) return;
    
    const messages = Array.from(document.querySelectorAll('.message'));
    const index = messages.indexOf(currentVisibleMessage);
    
    if (index >= 0 && index < messageHistory.length) {
        const content = messageHistory[index].content;
        editMessage(currentVisibleMessage, content);
    }
}

// 滚动到顶部
function scrollToTop() {
    const messagesDiv = document.getElementById('messages');
    messagesDiv.scrollTop = 0;
}

// 跳转到当前段落（消息）的顶部
function scrollToCurrentTop() {
    if (!currentVisibleMessage) return;
    
    const messagesDiv = document.getElementById('messages');
    const messageTop = currentVisibleMessage.offsetTop - messagesDiv.offsetTop;
    messagesDiv.scrollTop = messageTop;
}

// 跳转到当前段落（消息）的底部
function scrollToCurrentBottom() {
    if (!currentVisibleMessage) return;
    
    const messagesDiv = document.getElementById('messages');
    const messageBottom = currentVisibleMessage.offsetTop - messagesDiv.offsetTop 
                          + currentVisibleMessage.offsetHeight 
                          - messagesDiv.clientHeight 
                          + 20; // 留一些边距
    messagesDiv.scrollTop = Math.max(0, messageBottom);
}

// 跳转到全局顶部
function scrollToGlobalTop() {
    scrollToTop();
}

// 跳转到全局底部
function scrollToGlobalBottom() {
    scrollToBottom();
}

// 从后端获取会话完整历史
async function fetchSessionHistory(chatId) {
    if (!CONFIG.ENABLE_BACKEND_HISTORY) {
        return null;
    }
    try {
        const response = await fetch(`${CONFIG.API_URL}/api/sessions/${chatId}`);
        if (response.ok) {
            const data = await response.json();
            return data.history || null;
        }
    } catch (error) {
        console.log('[fetchSessionHistory] Failed to fetch:', error.message);
    }
    return null;
}

// 流式消息轮询更新
let streamingPollInterval = null;
let backendCheckAttempts = 0;

function startStreamingPoll(chatId) {
    // 清除之前的轮询
    if (streamingPollInterval) {
        clearInterval(streamingPollInterval);
    }
    backendCheckAttempts = 0;
    
    // 显示恢复提示（仅一次）
    const streamingDiv = document.getElementById('streamingMessage');
    if (streamingDiv) {
        const contentDiv = streamingDiv.querySelector('.streaming-content');
        if (contentDiv && !contentDiv.textContent.includes('🔄')) {
            contentDiv.textContent = (contentDiv.textContent || '') + '\n🔄 [已从页面恢复，正在同步AI运行状态...]';
        }
    }
    
    // 立即更新按钮状态
    updateSendButton();
    
    // 每秒检查一次 localStorage 和后端
    streamingPollInterval = setInterval(async () => {
        // 只有当当前显示的是被轮询的对话时才更新
        if (currentChatId !== chatId) {
            clearInterval(streamingPollInterval);
            streamingPollInterval = null;
            return;
        }
        
        const streamingState = localStorage.getItem(`streaming_${chatId}`);
        
        // 如果 streaming 状态不存在，说明已完成
        if (!streamingState) {
            console.log('[startStreamingPoll] Streaming completed, reloading chat', chatId);
            clearInterval(streamingPollInterval);
            streamingPollInterval = null;
            // 不要在这里自动 loadChat()，避免触发 resumeBackendStream() 导致对 /stream_state 的无限请求
            updateSendButton();
            return;
        }
        
        // 检查 localStorage 状态
        const state = JSON.parse(streamingState);
        const streamingDiv = document.getElementById('streamingMessage');
        
        // 每10秒尝试从后端获取完整历史
        if (CONFIG.ENABLE_BACKEND_HISTORY) {
            backendCheckAttempts++;
            if (backendCheckAttempts % 30 === 0) { // 每30 * 300ms = 9秒
                console.log('[startStreamingPoll] Attempt', backendCheckAttempts, '- fetching backend history for', chatId);
                const backendHistory = await fetchSessionHistory(chatId);
                console.log('[startStreamingPoll] Backend history result:', backendHistory === null ? '404/null' : `found ${backendHistory.length} messages`);
                
                if (backendHistory && backendHistory.length > 0) {
                    // 检查后端是否有比本地更新的消息
                    const lastBackendMsg = backendHistory[backendHistory.length - 1];
                    if (lastBackendMsg.role === 'assistant') {
                        console.log('[startStreamingPoll] Found completed response from backend');
                        const localRaw = localStorage.getItem(`chat_${chatId}`);
                        const localHistory = localRaw ? JSON.parse(localRaw) : [];
                        const mergedBackendHistory = mergeHistoryPreferLocal(localHistory, backendHistory);
                        
                        // 更新 localStorage 和 messageHistory
                        messageHistory = mergedBackendHistory;
                        localStorage.setItem(`chat_${chatId}`, JSON.stringify(mergedBackendHistory));
                        localStorage.removeItem(`streaming_${chatId}`);
                        
                        clearInterval(streamingPollInterval);
                        streamingPollInterval = null;
                        loadChat(chatId);
                        return;
                    }
                }
            }
        }
        
        if (streamingDiv) {
            const contentDiv = streamingDiv.querySelector('.streaming-content');
            if (contentDiv) {
                // 保护表格区域的状态行清理
                const normalizeStatusLines = (text) => {
                    if (!text) return '';
                    // 分离表格区域和非表格区域
                    const lines = text.split('\n');
                    const result = [];
                    let inTable = false;
                    
                    for (const line of lines) {
                        const isTableLine = line.trim().startsWith('|');
                        if (isTableLine) {
                            inTable = true;
                            result.push(line);
                        } else if (inTable && !isTableLine) {
                            inTable = false;
                            // 处理非表格行：移除状态提示
                            if (line && !line.match(/^(?:AI运行中|连接中断|已从页面恢复|等待后台结果同步|已思考)/)) {
                                result.push(line);
                            }
                        } else {
                            // 非表格区域：移除状态提示行
                            if (line && !line.match(/^(?:AI运行中|连接中断|已从页面恢复|等待后台结果同步|已思考)/)) {
                                result.push(line);
                            }
                        }
                    }
                    
                    return result.join('\n').trim();
                };
                const currentText = contentDiv.textContent || '';
                const baseText = normalizeStatusLines(currentText);
                const stateContent = normalizeStatusLines(state.content || '');
                const runningLine = '\n🔄 [AI运行中...]';
                const nextContent = (stateContent || baseText).trim();
                
                if (nextContent && nextContent !== baseText) {
                    const shouldStickToBottom = shouldAutoScroll();
                    contentDiv.textContent = nextContent + runningLine;
                    if (shouldStickToBottom) scrollToBottom();
                } else if (nextContent && !currentText.includes('[AI运行中...]')) {
                    contentDiv.textContent = nextContent + runningLine;
                } else if (!nextContent) {
                    contentDiv.textContent = '🤔 AI正在思考中...\n🔄 [已从页面恢复，等待服务器响应...]';
                }
            }
        }
        
        const ageMs = Date.now() - (state.timestamp || 0);
        const elapsed = Math.floor(ageMs / 1000);
        if (ageMs > CONFIG.STREAMING_STALE_MS) {
            console.log('[startStreamingPoll] Streaming state stale, clearing');
            localStorage.removeItem(`streaming_${chatId}`);
            if (streamingDiv) {
                const contentDiv = streamingDiv.querySelector('.streaming-content');
                if (contentDiv) {
                    contentDiv.textContent = '⏹️ 会话已过期，请开始新对话';
                }
                streamingDiv.classList.remove('streaming');
                streamingDiv.removeAttribute('id');
            }
            clearInterval(streamingPollInterval);
            streamingPollInterval = null;
            return;
        }
        if (streamingDiv && elapsed > 0) {
            const contentDiv = streamingDiv.querySelector('.streaming-content');
            if (contentDiv && !contentDiv.textContent.includes('已运行')) {
                const currentText = contentDiv.textContent || '';
                if (!currentText.includes('[已运行')) {
                    contentDiv.textContent = currentText.replace('AI运行中...', `AI运行中... 已运行${elapsed}秒`);
                }
            }
        }
        
        // 每次轮询都更新按钮状态
        updateSendButton();
    }, 300);
}

// 监听滚动事件 - 在 DOM 加载完成后绑定
document.addEventListener('DOMContentLoaded', () => {
    const messagesDiv = document.getElementById('messages');
    if (messagesDiv) {
        messagesDiv.addEventListener('scroll', trackVisibleMessage);
    }
    window.addEventListener('scroll', trackVisibleMessage);
    
    // 初始化时跟踪一次
    setTimeout(trackVisibleMessage, 100);
});

// 处理从其他页面返回时的状态恢复 (bfcache)
window.addEventListener('pageshow', (event) => {
    console.log('[pageshow] Event triggered, persisted:', event.persisted);
    
    // 如果是从缓存恢复的页面，需要重新加载状态
    if (event.persisted) {
        console.log('[pageshow] Page restored from bfcache, reloading state...');
        
        // 强制刷新历史列表
        loadChatHistory();
        
        // 如果有当前聊天ID，强制重新加载聊天内容
        if (currentChatId) {
            console.log('[pageshow] Force reloading current chat:', currentChatId);
            // 清空当前消息显示
            const messagesDiv = document.getElementById('messages');
            if (messagesDiv) {
                messagesDiv.innerHTML = '';
            }
            // 重新加载聊天
            loadChat(currentChatId);
        } else {
            // 如果没有当前聊天，尝试加载最近的
            loadMostRecentChat();
        }
        
        // 更新按钮状态
        updateSendButton();
    }
});

// 处理页面获得焦点时的状态刷新（非bfcache情况）
let lastFocusTime = 0;
window.addEventListener('focus', () => {
    const now = Date.now();
    // 避免频繁刷新（至少间隔1秒）
    if (now - lastFocusTime > 1000) {
        lastFocusTime = now;
        // 刷新历史列表显示
        loadChatHistory();
    }
});

// ========== Dashboard 控制中心 (聊天配置 + 开发者工具) ==========

// 聊天配置状态
let chatConfig = {
    model: 'auto',
    systemPrompt: '',
    temperature: 0.7
};

// 日志存储
let systemLogs = [];

// 使用统计
let usageStats = {
    totalTokens: 0,
    totalChats: 0,
    totalMessages: 0,
    avgResponseTime: 0,
    history: []
};

// ========== Nanobot 修复功能 ==========

async function checkAndFixNanobot() {
    // 检查并修复 nanobot 常见问题
    const container = event.target.closest('.fix-bug-container');
    const originalHTML = container ? container.innerHTML : '';
    
    try {
        // 显示检查中状态
        if (container) {
            container.innerHTML = '<i class="fas fa-spinner fa-spin"></i><span>检查中...</span>';
        }
        
        // 1. 检查是否需要修复
        const checkResponse = await fetch(`${CONFIG.API_URL}/api/fix/check`);
        const checkResult = await checkResponse.json();
        
        if (!checkResult.success) {
            showToast('检查失败: ' + checkResult.message, 'error');
            return;
        }
        
        if (!checkResult.needs_fix) {
            showToast('✅ 系统正常，无需修复', 'success');
            if (container) {
                container.innerHTML = '<i class="fas fa-check"></i><span>已检查</span>';
            }
            return;
        }
        
        // 2. 需要修复，应用修复
        if (container) {
            container.innerHTML = '<i class="fas fa-wrench"></i><span>修复中...</span>';
        }
        
        const fixResponse = await fetch(`${CONFIG.API_URL}/api/fix/apply`, { method: 'POST' });
        const fixResult = await fixResponse.json();
        
        if (fixResult.success) {
            showToast('✅ 修复成功！建议重启服务', 'success');
            if (container) {
                container.innerHTML = '<i class="fas fa-check"></i><span>已修复</span>';
            }
        } else {
            showToast('❌ 修复失败: ' + fixResult.message, 'error');
            if (container) {
                container.innerHTML = originalHTML;
            }
        }
        
    } catch (error) {
        console.error('[修复失败]', error);
        showToast('❌ 修复失败: ' + error.message, 'error');
        if (container) {
            container.innerHTML = originalHTML;
        }
    }
}

// ========== 增强版系统修复面板 ==========

let fixPanelOpen = false;
let currentFixIssues = [];

async function openFixPanel() {
    // 打开系统修复/状态面板
    if (fixPanelOpen) return;
    
    // 先扫描问题，根据状态等级决定显示内容
    const result = await scanFixIssuesQuiet();
    const statusLevel = result.status_level || 'INFO';
    const statusName = result.status_name || '系统状态';
    const issues = result.issues || [];
    const summary = result.summary || {};
    
    // 根据状态等级设置标题和样式
    let panelTitle, panelClass, panelIcon;
    let showFixButton = false;
    
    switch (statusLevel) {
        case 'CRITICAL':
            panelTitle = `<i class="fas fa-exclamation-circle"></i> 严重故障中心`;
            panelClass = 'fix-panel-header status-critical';
            panelIcon = 'fa-exclamation-circle';
            showFixButton = true;
            break;
        case 'ERROR':
            panelTitle = `<i class="fas fa-times-circle"></i> 系统错误中心`;
            panelClass = 'fix-panel-header status-error';
            panelIcon = 'fa-times-circle';
            showFixButton = true;
            break;
        case 'WARNING':
            panelTitle = `<i class="fas fa-exclamation-triangle"></i> 系统警告中心`;
            panelClass = 'fix-panel-header status-warning';
            panelIcon = 'fa-exclamation-triangle';
            showFixButton = issues.length > 0;
            break;
        case 'INFO':
        default:
            panelTitle = `<i class="fas fa-shield-alt"></i> 系统状态中心`;
            panelClass = 'fix-panel-header status-normal';
            panelIcon = 'fa-shield-alt';
            showFixButton = false;
            break;
    }
    
    // 创建面板
    const panel = document.createElement('div');
    panel.id = 'fix-panel';
    panel.className = 'fix-panel';
    
    // 根据状态生成内容
    const contentHtml = generatePanelContent(statusLevel, result);
    
    // 状态横幅样式
    const statusBannerStyle = statusLevel === 'INFO' ? 'style="display:flex;align-items:center;gap:8px;margin-left:auto;padding:4px 12px;background:rgba(16,185,129,0.2);border-radius:12px;font-size:12px;color:#34d399;"' : '';
    const statusBanner = statusLevel === 'INFO' ? '<i class="fas fa-check-circle"></i><span>系统运行正常</span>' : '';
    
    panel.innerHTML = `
        <div class="${panelClass}">
            <h3>${panelTitle}</h3>
            ${statusLevel === 'INFO' ? `<div class="status-header-banner" ${statusBannerStyle}>${statusBanner}</div>` : ''}
            <button class="fix-panel-close" onclick="closeFixPanel()">
                <i class="fas fa-times"></i>
            </button>
        </div>
        <div class="fix-panel-content" id="fixPanelContent">
            ${contentHtml}
        </div>
        <div class="fix-panel-footer" id="fixPanelFooter">
            <button class="fix-btn fix-btn-secondary" onclick="closeFixPanel()">关闭</button>
            ${showFixButton ? `<button class="fix-btn fix-btn-primary" id="fix-all-btn" onclick="applyAllFixes()">
                <i class="fas fa-magic"></i> 一键修复 (${issues.length})
            </button>` : ''}
        </div>
    `;
    
    document.body.appendChild(panel);
    fixPanelOpen = true;
    
    // 启用拖拽功能
    makePanelDraggable(panel, '.fix-panel-header');
    
    // 启用右键菜单
    makePanelContextMenu(panel);
    
    // 启用快捷键
    enablePanelShortcuts(panel, 'fix');
    
    // 根据状态加载内容
    if (statusLevel === 'INFO') {
        // 正常状态，加载系统状态
        await loadSystemStatus();
    } else if (issues.length > 0) {
        // 有问题，渲染问题列表
        currentFixIssues = issues;
        renderFixIssuesByLevel(result);
    }
}

function closeFixPanel() {
    // 关闭修复面板
    const panel = document.getElementById('fix-panel');
    if (panel) {
        panel.remove();
        fixPanelOpen = false;
    }
}

// 静默扫描问题（不显示面板）
async function scanFixIssuesQuiet() {
    try {
        const response = await fetch(`${CONFIG.API_URL}/api/fix/scan`);
        const result = await response.json();
        return result;
    } catch (error) {
        console.error('[scanFixIssuesQuiet] 扫描失败:', error);
        return { issues: [] };
    }
}

// 渲染问题内容模板
function renderIssuesContent() {
    return `
        <div class="fix-scan-status" id="fixScanStatus">
            <i class="fas fa-spinner fa-spin"></i>
            <span>正在扫描系统问题...</span>
        </div>
        <div class="fix-issues-list" id="fixIssuesList" style="display:none;"></div>
    `;
}

// 渲染正常状态内容模板
function renderStatusContent() {
    return `
        <div class="system-status-content">
            <div class="status-loading" id="statusLoading">
                <i class="fas fa-spinner fa-spin"></i>
                <span>正在加载系统状态...</span>
            </div>
            <div class="status-details" id="statusDetails" style="display:none;">
                <!-- 系统状态详情将在这里显示 -->
            </div>
        </div>
    `;
}

// 根据状态等级生成面板内容
function generatePanelContent(statusLevel, result) {
    const summary = result.summary || {};
    const issues = result.issues || [];
    
    switch (statusLevel) {
        case 'CRITICAL':
            return `
                <div class="status-critical-banner">
                    <i class="fas fa-exclamation-circle"></i>
                    <span>检测到 ${summary.critical || 0} 个严重问题，需要立即修复！</span>
                </div>
                <div class="fix-scan-status" id="fixScanStatus">
                    <i class="fas fa-spinner fa-spin"></i>
                    <span>正在分析问题详情...</span>
                </div>
                <div class="fix-issues-list" id="fixIssuesList" style="display:none;"></div>
            `;
        case 'ERROR':
            return `
                <div class="status-error-banner">
                    <i class="fas fa-times-circle"></i>
                    <span>检测到 ${summary.high || 0} 个错误，建议尽快修复</span>
                </div>
                <div class="fix-scan-status" id="fixScanStatus">
                    <i class="fas fa-spinner fa-spin"></i>
                    <span>正在分析问题详情...</span>
                </div>
                <div class="fix-issues-list" id="fixIssuesList" style="display:none;"></div>
            `;
        case 'WARNING':
            return `
                <div class="status-warning-banner">
                    <i class="fas fa-exclamation-triangle"></i>
                    <span>检测到 ${summary.medium || 0} 个警告，系统仍可正常运行</span>
                </div>
                <div class="fix-scan-status" id="fixScanStatus">
                    <i class="fas fa-spinner fa-spin"></i>
                    <span>正在分析问题详情...</span>
                </div>
                <div class="fix-issues-list" id="fixIssuesList" style="display:none;"></div>
            `;
        case 'INFO':
        default:
            return renderStatusContent();
    }
}

// 根据问题级别渲染问题列表
function renderFixIssuesByLevel(result) {
    const statusDiv = document.querySelector('.fix-scan-status');
    const listDiv = document.querySelector('.fix-issues-list');
    const fixAllBtn = document.getElementById('fix-all-btn');
    
    const issues = result.issues || [];
    const summary = result.summary || {};
    const statusLevel = result.status_level || 'INFO';
    
    if (issues.length === 0) {
        if (statusDiv) {
            statusDiv.innerHTML = `
                <i class="fas fa-check-circle" style="color:#10b981;"></i>
                <span>✅ 系统状态良好</span>
            `;
        }
        if (listDiv) listDiv.style.display = 'none';
        if (fixAllBtn) fixAllBtn.style.display = 'none';
        return;
    }
    
    // 显示统计
    const severityText = [];
    if (summary.critical) severityText.push(`${summary.critical}个严重`);
    if (summary.high) severityText.push(`${summary.high}个错误`);
    if (summary.medium) severityText.push(`${summary.medium}个警告`);
    if (summary.low) severityText.push(`${summary.low}个提示`);
    
    if (statusDiv) {
        const levelColors = {
            'CRITICAL': '#ef4444',
            'ERROR': '#f97316', 
            'WARNING': '#eab308',
            'INFO': '#10b981'
        };
        const color = levelColors[statusLevel] || '#10b981';
        statusDiv.innerHTML = `
            <i class="fas fa-info-circle" style="color:${color};"></i>
            <span>发现 ${issues.length} 个问题 (${severityText.join('、')})</span>
        `;
    }
    
    // 按严重级别分组渲染
    if (listDiv) {
        const severityOrder = ['critical', 'high', 'medium', 'low'];
        const severityLabels = {
            'critical': { text: '严重', color: '#ef4444', icon: 'fa-exclamation-circle' },
            'high': { text: '错误', color: '#f97316', icon: 'fa-times-circle' },
            'medium': { text: '警告', color: '#eab308', icon: 'fa-exclamation-triangle' },
            'low': { text: '提示', color: '#3b82f6', icon: 'fa-info-circle' }
        };
        
        let html = '';
        severityOrder.forEach(severity => {
            const severityIssues = issues.filter(i => i.severity === severity);
            if (severityIssues.length > 0) {
                const label = severityLabels[severity];
                html += `<div class="issue-severity-group" style="margin-bottom:16px;">`;
                html += `<h4 style="color:${label.color};margin-bottom:8px;font-size:14px;"><i class="fas ${label.icon}"></i> ${label.text} (${severityIssues.length})</h4>`;
                severityIssues.forEach(issue => {
                    // 构建详情HTML
                    let detailsHtml = '';
                    if (issue.details) {
                        // 权限问题 - 显示文件列表（新格式，包含详细权限信息）
                        if (issue.details.files && issue.details.files.length > 0) {
                            const fileDetails = issue.details.files.map(f => {
                                // 新格式：f 是对象 {path, current_mode, issue, suggestion}
                                if (typeof f === 'object' && f.path) {
                                    return `
                                        <div style="margin-bottom:8px;padding:8px;background:rgba(0,0,0,0.15);border-radius:4px;">
                                            <div style="font-size:11px;color:#e2e8f0;font-family:monospace;word-break:break-all;">
                                                <i class="fas fa-file-alt" style="color:#64748b;margin-right:4px;"></i>${f.path}
                                            </div>
                                            <div style="font-size:11px;color:#fca5a5;margin-top:4px;">
                                                <i class="fas fa-exclamation-triangle" style="margin-right:4px;"></i>当前权限: ${f.current_mode}
                                            </div>
                                            <div style="font-size:11px;color:#94a3b8;margin-top:2px;">${f.issue}</div>
                                            <div style="font-size:11px;color:#34d399;margin-top:2px;">
                                                <i class="fas fa-wrench" style="margin-right:4px;"></i>${f.suggestion}
                                            </div>
                                        </div>
                                    `;
                                }
                                // 旧格式兼容：f 是字符串路径
                                return `<div style="font-size:11px;color:#e2e8f0;font-family:monospace;word-break:break-all;padding:2px 0;"><i class="fas fa-file-alt" style="color:#64748b;margin-right:4px;"></i>${f}</div>`;
                            }).join('');
                            
                            detailsHtml += `<div class="issue-details-files" style="margin-top:8px;padding:8px;background:rgba(0,0,0,0.2);border-radius:4px;">
                                <div style="font-size:12px;color:#94a3b8;margin-bottom:4px;"><i class="fas fa-folder-open"></i> 涉及文件：</div>
                                ${fileDetails}
                            </div>`;
                            
                            // 显示总体描述
                            if (issue.details.description) {
                                detailsHtml += `<div style="margin-top:8px;font-size:12px;color:#eab308;"><i class="fas fa-info-circle"></i> ${issue.details.description}</div>`;
                            }
                        }
                        // API配置问题 - 显示具体缺失项
                        if (issue.details.issues && issue.details.issues.length > 0) {
                            detailsHtml += `<div class="issue-details-list" style="margin-top:8px;padding:8px;background:rgba(0,0,0,0.2);border-radius:4px;">
                                <div style="font-size:12px;color:#94a3b8;margin-bottom:4px;"><i class="fas fa-list-ul"></i> 具体问题：</div>
                                ${issue.details.issues.map(i => `<div style="font-size:11px;color:#e2e8f0;padding:2px 0;"><i class="fas fa-angle-right" style="color:#64748b;margin-right:4px;"></i>${i}</div>`).join('')}
                            </div>`;
                        }
                        // 缓存问题 - 显示大小信息
                        if (issue.details.size && issue.details.max_size) {
                            const sizeMB = (issue.details.size / 1024 / 1024).toFixed(2);
                            const maxMB = (issue.details.max_size / 1024 / 1024).toFixed(0);
                            detailsHtml += `<div class="issue-details-size" style="margin-top:8px;padding:8px;background:rgba(0,0,0,0.2);border-radius:4px;">
                                <div style="font-size:12px;color:#94a3b8;"><i class="fas fa-hdd"></i> 缓存大小: <span style="color:#eab308;">${sizeMB} MB</span> / ${maxMB} MB</div>
                            </div>`;
                        }
                        // 模型同步问题
                        if (issue.details.config_only || issue.details.file_only) {
                            detailsHtml += `<div class="issue-details-sync" style="margin-top:8px;padding:8px;background:rgba(0,0,0,0.2);border-radius:4px;">
                                ${issue.details.config_only ? `<div style="font-size:11px;color:#e2e8f0;"><i class="fas fa-file" style="color:#64748b;margin-right:4px;"></i>仅配置中: ${issue.details.config_only.join(', ')}</div>` : ''}
                                ${issue.details.file_only ? `<div style="font-size:11px;color:#e2e8f0;"><i class="fas fa-file" style="color:#64748b;margin-right:4px;"></i>仅文件中: ${issue.details.file_only.join(', ')}</div>` : ''}
                            </div>`;
                        }
                    }
                    
                    html += `
                        <div class="fix-issue-item" style="border-left:3px solid ${label.color};padding-left:12px;margin-bottom:12px;">
                            <div class="issue-name" style="font-weight:500;color:#e2e8f0;">${issue.name}</div>
                            <div class="issue-desc" style="font-size:13px;color:#94a3b8;margin-top:4px;">${issue.description}</div>
                            ${detailsHtml}
                            ${issue.auto_fixable ? '<span class="auto-fixable" style="font-size:12px;color:#10b981;margin-top:4px;display:inline-block;"><i class="fas fa-magic"></i> 可自动修复</span>' : '<span class="manual-fix" style="font-size:12px;color:#eab308;margin-top:4px;display:inline-block;"><i class="fas fa-hand-paper"></i> 需手动修复</span>'}
                        </div>
                    `;
                });
                html += `</div>`;
            }
        });
        
        listDiv.innerHTML = html;
        listDiv.style.display = 'block';
    }
}

// 加载系统状态（正常状态时使用）
async function loadSystemStatus() {
    const loadingDiv = document.getElementById('statusLoading');
    const detailsDiv = document.getElementById('statusDetails');
    
    if (!loadingDiv || !detailsDiv) return;
    
    try {
        // 获取详细系统状态
        const response = await fetch(`${CONFIG.API_URL}/api/system/status`);
        const data = response.ok ? await response.json() : null;
        
        loadingDiv.style.display = 'none';
        detailsDiv.style.display = 'block';
        
        if (!data || !data.success) {
            detailsDiv.innerHTML = '<div class="status-error">获取系统状态失败</div>';
            return;
        }
        
        // 构建状态详情HTML
        let html = '<div class="status-sections">';
        
        // 1. 任务调度器状态（详细版）
        const tasks = data.tasks || {};
        const taskList = tasks.tasks || [];
        html += renderStatusSection('任务调度器', 'fa-tasks', [
            { label: '总任务数', value: tasks.total || 0 },
            { label: '已启用', value: tasks.enabled || 0 },
            { label: '待执行', value: tasks.pending || 0 },
            { label: '运行中', value: tasks.running || 0 }
        ]);
        
        // 显示任务列表（如果有）
        if (taskList.length > 0) {
            html += '<div class="status-section"><h4 class="status-section-title"><i class="fas fa-list"></i> 待办任务列表</h4><div class="task-list">';
            taskList.forEach(task => {
                const statusIcon = task.status === 'running' ? '<i class="fas fa-play-circle" style="color:#3b82f6"></i>' : 
                                  task.status === 'pending' ? '<i class="fas fa-clock" style="color:#eab308"></i>' : 
                                  '<i class="fas fa-check-circle" style="color:#10b981"></i>';
                html += `
                    <div class="task-item" style="padding:8px;border-bottom:1px solid rgba(255,255,255,0.1);">
                        <div style="display:flex;justify-content:space-between;align-items:center;">
                            <span><strong>${statusIcon} ${task.name}</strong></span>
                            <span style="font-size:12px;color:#94a3b8;">${task.type}</span>
                        </div>
                        <div style="font-size:12px;color:#64748b;margin-top:4px;">${task.description || ''}</div>
                        ${task.next_run ? `<div style="font-size:11px;color:#64748b;margin-top:2px;"><i class="far fa-clock"></i> 下次运行: ${new Date(task.next_run).toLocaleString('zh-CN')}</div>` : ''}
                    </div>
                `;
            });
            html += '</div></div>';
        }
        
        // 2. 健康报告状态
        const health = data.health || {};
        let healthBadge = '<span class="status-badge" style="background:rgba(100,100,100,0.3);">未知</span>';
        if (health.level === 'excellent') healthBadge = '<span class="status-badge" style="background:rgba(16,185,129,0.3);color:#34d399;">优秀</span>';
        else if (health.level === 'good') healthBadge = '<span class="status-badge" style="background:rgba(16,185,129,0.3);color:#34d399;">良好</span>';
        else if (health.level === 'fair') healthBadge = '<span class="status-badge" style="background:rgba(234,179,8,0.3);color:#fde047;">一般</span>';
        else if (health.level === 'poor') healthBadge = '<span class="status-badge" style="background:rgba(239,68,68,0.3);color:#fca5a5;">较差</span>';
        
        html += renderStatusSection('健康报告', 'fa-heartbeat', [
            { label: '健康分数', value: health.score !== null ? `${health.score}/100 ⭐` : '未检测' },
            { label: '健康等级', value: healthBadge },
            { label: '最近检查', value: health.last_check ? new Date(health.last_check).toLocaleString('zh-CN') : '从未' }
        ]);
        
        // 3. 用户偏好设置
        const preferences = data.preferences || [];
        if (preferences.length > 0) {
            html += renderStatusSection('用户偏好', 'fa-user-cog', 
                preferences.map((pref, idx) => ({ 
                    label: `偏好 ${idx + 1}`, 
                    value: `<span style="color:#94a3b8;">${pref}</span>` 
                }))
            );
        }
        
        // 4. 系统资源
        const resources = data.resources || {};
        const memoryPercent = resources.memory_percent || 0;
        const diskPercent = resources.disk_percent || 0;
        
        let memoryColor = '#10b981';
        if (memoryPercent > 80) memoryColor = '#ef4444';
        else if (memoryPercent > 60) memoryColor = '#eab308';
        
        let diskColor = '#10b981';
        if (diskPercent > 80) diskColor = '#ef4444';
        else if (diskPercent > 60) diskColor = '#eab308';
        
        html += renderStatusSection('系统资源', 'fa-server', [
            { label: '内存使用', value: `<span style="color:${memoryColor}">${memoryPercent}%</span> (${resources.memory_used_gb}/${resources.memory_total_gb} GB)` },
            { label: '磁盘使用', value: `<span style="color:${diskColor}">${diskPercent}%</span> (可用 ${resources.disk_free_gb} GB)` }
        ]);
        
        // 5. 修复规则状态
        html += renderStatusSection('修复规则', 'fa-tools', [
            { label: '可用规则', value: '7' },
            { label: '系统健康度', value: '<span class="status-badge" style="background:rgba(16,185,129,0.3);color:#34d399;">监控中</span>' }
        ]);
        
        // 6. 最后更新时间
        html += `<div class="status-update-time">
            <i class="far fa-clock"></i> 最后更新: ${new Date(data.timestamp).toLocaleString('zh-CN')}
        </div>`;
        
        html += '</div>';
        detailsDiv.innerHTML = html;
        
    } catch (error) {
        console.error('[loadSystemStatus] 加载失败:', error);
        loadingDiv.innerHTML = '<span class="status-error">加载系统状态失败</span>';
    }
}

// 渲染状态部分
function renderStatusSection(title, icon, items) {
    const itemsHtml = items.map(item => `
        <div class="status-item">
            <span class="status-item-label">${item.label}</span>
            <span class="status-item-value">${item.value}</span>
        </div>
    `).join('');
    
    return `
        <div class="status-section">
            <h4 class="status-section-title"><i class="fas ${icon}"></i> ${title}</h4>
            <div class="status-items">${itemsHtml}</div>
        </div>
    `;
}

// 更新修复按钮状态（根据系统状态等级）
async function updateFixButtonStatus() {
    try {
        const result = await scanFixIssuesQuiet();
        const statusLevel = result.status_level || 'INFO';
        const statusName = result.status_name || '系统状态';
        const issues = result.issues || [];
        
        const container = document.querySelector('.fix-bug-container');
        
        if (container) {
            // 清除所有状态类
            container.classList.remove('status-normal', 'status-warning', 'status-error', 'has-issues');
            
            // 根据状态等级设置样式
            let iconClass = 'fas fa-shield-alt';
            let buttonText = '系统状态';
            
            switch (statusLevel) {
                case 'CRITICAL':
                    container.classList.add('has-issues');
                    iconClass = 'fas fa-exclamation-circle';
                    buttonText = '严重故障';
                    break;
                case 'ERROR':
                    container.classList.add('status-error');
                    iconClass = 'fas fa-times-circle';
                    buttonText = '系统错误';
                    break;
                case 'WARNING':
                    container.classList.add('status-warning');
                    iconClass = 'fas fa-exclamation-triangle';
                    buttonText = '系统警告';
                    break;
                case 'INFO':
                default:
                    container.classList.add('status-normal');
                    iconClass = 'fas fa-shield-alt';
                    buttonText = '系统状态';
                    break;
            }
            
            // 更新文字
            const textSpan = container.querySelector('span:not(.fix-bug-icon)');
            if (textSpan) {
                textSpan.textContent = buttonText;
            }
            
            // 更新图标
            const icon = container.querySelector('i');
            if (icon) {
                icon.className = iconClass;
            }
            
            // 存储状态信息
            container.dataset.statusLevel = statusLevel;
            container.dataset.statusName = statusName;
            container.dataset.issues = JSON.stringify(issues);
        }
    } catch (error) {
        console.error('[updateFixButtonStatus] 更新失败:', error);
    }
}

// 页面加载时检查按钮状态
document.addEventListener('DOMContentLoaded', function() {
    // 延迟执行，确保DOM完全加载
    setTimeout(updateFixButtonStatus, 2000);
    // 定期更新（每60秒）
    setInterval(updateFixButtonStatus, 60000);
});

async function scanFixIssues() {
    // 扫描系统问题
    try {
        const response = await fetch(`${CONFIG.API_URL}/api/fix/scan`);
        const result = await response.json();
        
        if (!result.success) {
            updateFixPanelError(result.message);
            return;
        }
        
        currentFixIssues = result.issues || [];
        renderFixIssues(result);
        
    } catch (error) {
        updateFixPanelError('扫描失败: ' + error.message);
    }
}

function updateFixPanelError(message) {
    // 更新面板显示错误
    const statusDiv = document.querySelector('.fix-scan-status');
    if (statusDiv) {
        statusDiv.innerHTML = `<i class="fas fa-exclamation-triangle" style="color:#ef4444;"></i> <span>${message}</span>`;
    }
}

function renderFixIssues(result) {
    // 渲染修复问题列表
    const statusDiv = document.querySelector('.fix-scan-status');
    const listDiv = document.querySelector('.fix-issues-list');
    const fixAllBtn = document.getElementById('fix-all-btn');
    
    const issues = result.issues || [];
    const summary = result.summary || {};
    
    if (issues.length === 0) {
        statusDiv.innerHTML = `
            <i class="fas fa-check-circle" style="color:#10b981;"></i>
            <span>✅ 系统状态良好，未发现需要修复的问题</span>
        `;
        listDiv.style.display = 'none';
        fixAllBtn.style.display = 'none';
        return;
    }
    
    // 显示统计
    const severityText = [];
    if (summary.critical) severityText.push(`${summary.critical}个严重`);
    if (summary.high) severityText.push(`${summary.high}个高危`);
    if (summary.medium) severityText.push(`${summary.medium}个中危`);
    if (summary.low) severityText.push(`${summary.low}个低危`);
    
    statusDiv.innerHTML = `
        <i class="fas fa-exclamation-circle" style="color:#f59e0b;"></i>
        <span>发现 ${issues.length} 个问题${severityText.length > 0 ? ' (' + severityText.join('、') + ')' : ''}</span>
    `;
    
    // 按分类渲染问题
    const categories = result.categories || {};
    let html = '';
    
    const categoryNames = {
        'code': '代码问题',
        'config': '配置问题',
        'permission': '权限问题',
        'cache': '缓存问题',
        'database': '数据库问题',
        'maintenance': '维护任务',
        'unknown': '其他问题'
    };
    
    for (const [cat, catIssues] of Object.entries(categories)) {
        if (catIssues.length === 0) continue;
        
        html += `
            <div class="fix-category">
                <h4>${categoryNames[cat] || cat} (${catIssues.length})</h4>
        `;
        
        for (const issue of catIssues) {
            const severityClass = issue.severity === 'critical' ? 'severity-critical' : 
                                 issue.severity === 'high' ? 'severity-high' : 
                                 issue.severity === 'medium' ? 'severity-medium' : 'severity-low';
            
            const canAutoFix = issue.auto_fixable !== false;
            
            html += `
                <div class="fix-issue-item ${severityClass}" data-rule-id="${issue.rule_id}">
                    <div class="fix-issue-header">
                        <span class="fix-issue-name">${issue.name}</span>
                        <span class="fix-issue-severity">${issue.severity}</span>
                    </div>
                    <div class="fix-issue-desc">${issue.description}</div>
                    <div class="fix-issue-actions">
                        ${canAutoFix ? `
                            <button class="fix-btn-sm fix-btn-primary" onclick="applySingleFix('${issue.rule_id}', this)">
                                <i class="fas fa-wrench"></i> 修复
                            </button>
                        ` : `
                            <span class="fix-manual-hint"><i class="fas fa-info-circle"></i> 需手动修复</span>
                        `}
                    </div>
                </div>
            `;
        }
        
        html += '</div>';
    }
    
    listDiv.innerHTML = html;
    listDiv.style.display = 'block';
    
    // 显示一键修复按钮（如果有可自动修复的问题）
    const autoFixableCount = issues.filter(i => i.auto_fixable !== false).length;
    if (autoFixableCount > 0) {
        fixAllBtn.style.display = 'inline-flex';
        fixAllBtn.innerHTML = `<i class="fas fa-magic"></i> 一键修复全部 (${autoFixableCount})`;
    }
}

async function applySingleFix(ruleId, btn) {
    // 应用单个修复
    const originalText = btn.innerHTML;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 修复中...';
    btn.disabled = true;
    
    try {
        const response = await fetch(`${CONFIG.API_URL}/api/fix/apply/${ruleId}`, { method: 'POST' });
        const result = await response.json();
        
        if (result.success) {
            showToast(`✅ ${result.message}`, 'success');
            btn.innerHTML = '<i class="fas fa-check"></i> 已修复';
            btn.classList.remove('fix-btn-primary');
            btn.classList.add('fix-btn-success');
            
            // 如果需要重启，显示提示
            if (result.requires_restart) {
                setTimeout(() => {
                    showToast('⚠️ 修复已应用，建议重启服务', 'warning');
                }, 1000);
            }
        } else {
            showToast('❌ ' + result.message, 'error');
            btn.innerHTML = originalText;
            btn.disabled = false;
        }
        
    } catch (error) {
        showToast('❌ 修复失败: ' + error.message, 'error');
        btn.innerHTML = originalText;
        btn.disabled = false;
    }
}

async function applyAllFixes() {
    // 应用所有自动修复
    const btn = document.getElementById('fix-all-btn');
    const originalText = btn.innerHTML;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 批量修复中...';
    btn.disabled = true;
    
    try {
        const response = await fetch(`${CONFIG.API_URL}/api/fix/apply-all?auto_only=true`, { method: 'POST' });
        const result = await response.json();
        
        if (result.success) {
            const summary = result.summary || {};
            showToast(`✅ 批量修复完成：${summary.success}成功 ${summary.failed}失败`, 'success');
            
            // 重新扫描
            await scanFixIssues();
        } else {
            showToast('❌ ' + result.message, 'error');
            btn.innerHTML = originalText;
            btn.disabled = false;
        }
        
    } catch (error) {
        showToast('❌ 批量修复失败: ' + error.message, 'error');
        btn.innerHTML = originalText;
        btn.disabled = false;
    }
}

// 保留旧函数用于兼容
async function checkAndFixNanobot() {
    // 旧版修复函数 - 现在打开修复面板
    await openFixPanel();
}

// Toast 提示函数（如果不存在则创建）
if (typeof showToast !== 'function') {
    window.showToast = function(message, type = 'info') {
        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;
        toast.innerHTML = message;
        toast.style.cssText = `
            position: fixed;
            bottom: 20px;
            right: 20px;
            padding: 12px 24px;
            border-radius: 8px;
            background: ${type === 'success' ? '#10b981' : type === 'error' ? '#ef4444' : '#3b82f6'};
            color: white;
            z-index: 10000;
            animation: fadeIn 0.3s ease;
        `;
        document.body.appendChild(toast);
        setTimeout(() => {
            toast.style.animation = 'fadeOut 0.3s ease';
            setTimeout(() => toast.remove(), 300);
        }, 3000);
    };
}

// 初始化控制中心
function initDashboard() {
    loadChatConfig();
    loadApiConfig();
    loadUsageStats();
    refreshLogs();
}

// ========== 聊天配置功能 ==========

function updateTempDisplay() {
    const temp = document.getElementById('chat-temperature').value;
    document.getElementById('temp-value').textContent = temp;
}

function changeChatModel() {
    const model = document.getElementById('chat-model-select').value;
    chatConfig.model = model;
    console.log('[Dashboard] Model changed to:', model);
}

function applyChatConfig() {
    chatConfig = {
        model: document.getElementById('chat-model-select').value,
        systemPrompt: document.getElementById('chat-system-prompt').value,
        temperature: parseFloat(document.getElementById('chat-temperature').value)
    };
    
    // 保存到localStorage
    localStorage.setItem('nanobot_chat_config', JSON.stringify(chatConfig));
    
    // 显示成功状态
    const status = document.getElementById('chat-config-status');
    status.textContent = '✓ 配置已应用到当前对话';
    status.className = 'config-status success';
    
    setTimeout(() => {
        status.className = 'config-status';
    }, 3000);
    
    console.log('[Dashboard] Chat config applied:', chatConfig);
}

function loadChatConfig() {
    const saved = localStorage.getItem('nanobot_chat_config');
    if (saved) {
        chatConfig = JSON.parse(saved);
        document.getElementById('chat-model-select').value = chatConfig.model || 'auto';
        document.getElementById('chat-system-prompt').value = chatConfig.systemPrompt || '';
        document.getElementById('chat-temperature').value = chatConfig.temperature || 0.7;
        document.getElementById('temp-value').textContent = chatConfig.temperature || 0.7;
    }
}

// 获取当前聊天配置（供聊天功能使用）
function getChatConfig() {
    return chatConfig;
}

// ========== 快速操作功能 ==========

function loadRecentChats() {
    // 触发侧边栏历史加载
    loadChatHistory();
    toggleDashboard(false);
    showNotification('已加载最近会话');
}

function clearCurrentChat() {
    if (confirm('确定要清空当前对话吗？')) {
        document.getElementById('messages').innerHTML = '';
        messageHistory = [];
        saveCurrentChat();
        toggleDashboard(false);
        showNotification('当前对话已清空');
    }
}

function importChat() {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json';
    input.onchange = async (e) => {
        const file = e.target.files[0];
        if (!file) return;
        
        try {
            const text = await file.text();
            const data = JSON.parse(text);
            
            if (data.messages) {
                messageHistory = data.messages;
                currentChatId = data.sessionId || Date.now().toString();
                renderMessages();
                saveCurrentChat();
                toggleDashboard(false);
                showNotification('对话已导入');
            }
        } catch (err) {
            alert('导入失败：' + err.message);
        }
    };
    input.click();
}

function clearAllChats() {
    if (confirm('⚠️ 确定要清除所有历史对话吗？此操作不可恢复！')) {
        localStorage.removeItem('nanobot_chat_history');
        localStorage.removeItem('nanobot_chat_order');
        chatHistory = [];
        document.getElementById('messages').innerHTML = '';
        messageHistory = [];
        startNewChat();
        toggleDashboard(false);
        showNotification('所有对话已清除');
    }
}

// ========== 系统日志功能 ==========

function addLog(message, level = 'info') {
    const entry = {
        time: new Date().toLocaleTimeString(),
        message,
        level
    };
    systemLogs.push(entry);
    
    // 保留最近100条
    if (systemLogs.length > 100) {
        systemLogs = systemLogs.slice(-100);
    }
    
    renderLogs();
}

function renderLogs() {
    const container = document.getElementById('system-logs');
    if (!container) return;
    
    const level = document.getElementById('log-level')?.value || 'all';
    
    let logs = systemLogs;
    if (level !== 'all') {
        logs = systemLogs.filter(l => l.level === level);
    }
    
    container.innerHTML = logs.map(l => 
        `<div class="log-entry ${l.level}">[${l.time}] ${l.message}</div>`
    ).join('');
    
    // 滚动到底部
    container.scrollTop = container.scrollHeight;
}

function refreshLogs() {
    // 添加测试日志
    addLog('系统日志已刷新', 'info');
    renderLogs();
}

function clearLogs() {
    systemLogs = [];
    renderLogs();
    addLog('日志已清空', 'info');
}

function downloadLogs() {
    const text = systemLogs.map(l => `[${l.time}] [${l.level.toUpperCase()}] ${l.message}`).join('\n');
    const blob = new Blob([text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `nanobot_logs_${Date.now()}.txt`;
    a.click();
    URL.revokeObjectURL(url);
}

// ========== 工作区功能 ==========

function openWorkspaceFolder() {
    // 尝试打开工作区目录
    window.open('file://' + WORKSPACE, '_blank');
    addLog('尝试打开工作区目录', 'info');
}

function createNewFile() {
    const name = prompt('请输入文件名：');
    if (name) {
        addLog(`创建新文件: ${name}`, 'info');
        showNotification(`文件 ${name} 已创建（模拟）`);
    }
}

// ========== API配置功能 ==========

function saveApiConfig() {
    const config = {
        openaiKey: document.getElementById('api-key-openai').value,
        claudeKey: document.getElementById('api-key-claude').value,
        proxy: document.getElementById('api-proxy').value
    };
    
    // 加密存储（简单Base64，生产环境需要更强加密）
    localStorage.setItem('nanobot_api_config', btoa(JSON.stringify(config)));
    
    showNotification('API配置已保存');
    addLog('API配置已更新', 'info');
}

function loadApiConfig() {
    const saved = localStorage.getItem('nanobot_api_config');
    if (saved) {
        try {
            const config = JSON.parse(atob(saved));
            document.getElementById('api-key-openai').value = config.openaiKey || '';
            document.getElementById('api-key-claude').value = config.claudeKey || '';
            document.getElementById('api-proxy').value = config.proxy || '';
        } catch (e) {
            console.error('Failed to load API config:', e);
        }
    }
}

// ========== 使用统计功能 ==========

function updateUsageStats(tokens, responseTime) {
    usageStats.totalTokens += tokens || 0;
    usageStats.totalMessages++;
    
    // 计算平均响应时间
    const prevAvg = usageStats.avgResponseTime;
    const count = usageStats.totalMessages;
    usageStats.avgResponseTime = (prevAvg * (count - 1) + (responseTime || 0)) / count;
    
    // 记录历史
    usageStats.history.push({
        time: new Date().toISOString(),
        tokens: tokens || 0,
        responseTime: responseTime || 0
    });
    
    // 保留最近100条
    if (usageStats.history.length > 100) {
        usageStats.history = usageStats.history.slice(-100);
    }
    
    localStorage.setItem('nanobot_usage_stats', JSON.stringify(usageStats));
    renderUsageStats();
}

function loadUsageStats() {
    const saved = localStorage.getItem('nanobot_usage_stats');
    if (saved) {
        usageStats = JSON.parse(saved);
    }
    
    // 从聊天历史计算对话数
    const historyOrder = localStorage.getItem('nanobot_chat_order');
    if (historyOrder) {
        usageStats.totalChats = JSON.parse(historyOrder).length;
    }
    
    renderUsageStats();
}

function renderUsageStats() {
    document.getElementById('stat-tokens').textContent = usageStats.totalTokens.toLocaleString();
    document.getElementById('stat-chats').textContent = usageStats.totalChats;
    document.getElementById('stat-messages').textContent = usageStats.totalMessages;
    document.getElementById('stat-avg-time').textContent = usageStats.avgResponseTime.toFixed(1) + 's';
}

// 通知提示
function showNotification(message) {
    // 创建临时通知元素
    const notif = document.createElement('div');
    notif.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        background: var(--primary-color);
        color: white;
        padding: 12px 20px;
        border-radius: 8px;
        z-index: 10000;
        font-size: 14px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.3);
        animation: slideIn 0.3s ease;
    `;
    notif.textContent = message;
    document.body.appendChild(notif);
    
    setTimeout(() => {
        notif.style.animation = 'slideOut 0.3s ease';
        setTimeout(() => notif.remove(), 300);
    }, 3000);
}

// ========== 原始toggleDashboard函数（控制面板显示/隐藏） ==========

function toggleDashboard(show) {
    const dashboard = document.getElementById('dashboardPanel');
    const container = document.querySelector('.container');
    const chatHeader = document.querySelector('.chat-header');
    const messages = document.getElementById('messages');
    const inputArea = document.querySelector('.input-container');
    const favoritesPanel = document.getElementById('favoritesPanel');
    const homePage = document.getElementById('homePage');
    
    if (!dashboard) {
        console.error('dashboardPanel not found!');
        return;
    }
    
    if (show) {
        // 隐藏其他面板
        if (homePage) homePage.classList.remove('active');
        if (favoritesPanel) favoritesPanel.classList.remove('active');
        
        // 隐藏聊天相关元素
        if (chatHeader) chatHeader.style.display = 'none';
        if (messages) messages.style.display = 'none';
        if (inputArea) inputArea.style.display = 'none';
        if (container) container.style.display = 'none';
        
        // 显示Dashboard
        dashboard.style.cssText = 'display: flex !important; flex-direction: column !important; position: relative !important; width: 100% !important; height: auto !important; z-index: 9999 !important; background: #0f0f1a !important; overflow-y: auto !important; padding: 20px !important; box-sizing: border-box !important;';
        dashboard.classList.add('active');
        
        // 初始化Dashboard
        initDashboard();
    } else {
        // 隐藏Dashboard
        dashboard.style.cssText = 'display: none !important;';
        dashboard.classList.remove('active');
        
        // 恢复聊天相关元素
        if (chatHeader) chatHeader.style.display = '';
        if (messages) messages.style.display = '';
        if (inputArea) inputArea.style.display = '';
        if (container) container.style.display = '';
    }
}

// ========== 权限请求管理系统 ==========

// 权限类型配置
const PERMISSION_CONFIG = {
    internet: { icon: 'fa-globe', iconClass: 'internet', title: '访问互联网' },
    web_browse: { icon: 'fa-browser', iconClass: 'internet', title: '浏览网页' },
    file_read: { icon: 'fa-file-alt', iconClass: 'file', title: '读取文件' },
    file_write: { icon: 'fa-file-pen', iconClass: 'file', title: '写入文件' },
    code_exec: { icon: 'fa-code', iconClass: 'code', title: '执行代码' },
    system_cmd: { icon: 'fa-terminal', iconClass: 'system', title: '系统命令' },
    api_call: { icon: 'fa-plug', iconClass: 'internet', title: '调用外部API' },
    dangerous: { icon: 'fa-exclamation-triangle', iconClass: 'danger', title: '危险操作' }
};

// 权限管理器
const PermissionManager = {
    container: null,
    pendingRequests: new Map(),
    wsConnection: null,
    reconnectAttempts: 0,
    maxReconnectAttempts: 5,

    // 初始化
    init() {
        console.log('[PermissionManager] 初始化权限管理系统');
        this.createContainer();
        this.connectWebSocket();
        this.startPolling();
    },

    // 创建弹窗容器
    createContainer() {
        if (document.getElementById('permissionToastContainer')) return;
        
        this.container = document.createElement('div');
        this.container.id = 'permissionToastContainer';
        this.container.className = 'permission-toast-container';
        document.body.appendChild(this.container);
        console.log('[PermissionManager] 弹窗容器已创建');
    },

    // WebSocket连接
    connectWebSocket() {
        if (this.wsConnection?.readyState === WebSocket.OPEN) return;
        
        try {
            const wsUrl = `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/permission`;
            this.wsConnection = new WebSocket(wsUrl);
            
            this.wsConnection.onopen = () => {
                console.log('[PermissionManager] WebSocket已连接');
                this.reconnectAttempts = 0;
                // 订阅当前会话
                this.wsConnection.send(JSON.stringify({
                    action: 'subscribe',
                    session_id: currentSession || currentChatId
                }));
            };
            
            this.wsConnection.onmessage = (event) => {
                const data = JSON.parse(event.data);
                this.handleWebSocketMessage(data);
            };
            
            this.wsConnection.onclose = () => {
                console.log('[PermissionManager] WebSocket已断开');
                // 自动重连
                if (this.reconnectAttempts < this.maxReconnectAttempts) {
                    this.reconnectAttempts++;
                    setTimeout(() => this.connectWebSocket(), 2000 * this.reconnectAttempts);
                }
            };
            
            this.wsConnection.onerror = (error) => {
                console.error('[PermissionManager] WebSocket错误:', error);
            };
        } catch (e) {
            console.error('[PermissionManager] WebSocket连接失败:', e);
        }
    },

    // 处理WebSocket消息
    handleWebSocketMessage(data) {
        console.log('[PermissionManager] 收到消息:', data);
        
        if (data.type === 'pending_list') {
            // 更新待处理列表
            data.requests.forEach(req => this.showPermissionToast(req));
        } else if (data.type === 'new_request') {
            // 新权限请求
            this.showPermissionToast(data.request);
        } else if (data.type === 'decision_result') {
            // 决策结果
            this.removeToast(data.request_id);
        }
    },

    // 轮询备选方案
    startPolling() {
        setInterval(async () => {
            if (this.wsConnection?.readyState !== WebSocket.OPEN) {
                try {
                    const response = await fetch(`${CONFIG.API_URL}/api/permission/pending?session_id=${currentSession || currentChatId}`);
                    const data = await response.json();
                    if (data.success && data.requests.length > 0) {
                        data.requests.forEach(req => {
                            if (!this.pendingRequests.has(req.request_id)) {
                                this.showPermissionToast(req);
                            }
                        });
                    }
                } catch (e) {
                    // 静默失败
                }
            }
        }, 5000);
    },

    // 显示权限请求弹窗
    showPermissionToast(request) {
        if (this.pendingRequests.has(request.request_id)) return;
        
        const config = PERMISSION_CONFIG[request.permission_type] || PERMISSION_CONFIG.internet;
        
        const toast = document.createElement('div');
        toast.className = 'permission-toast';
        toast.id = `permission-${request.request_id}`;
        toast.innerHTML = `
            <div class="permission-header">
                <div class="permission-icon ${config.iconClass}">
                    <i class="fas ${config.icon}"></i>
                </div>
                <div>
                    <div class="permission-title">${request.title || config.title}</div>
                    <div class="permission-subtitle">${request.description || '模型请求权限'}</div>
                </div>
            </div>
            ${request.details ? `<div class="permission-details">${this.escapeHtml(request.details)}</div>` : ''}
            <div class="permission-actions">
                <button class="permission-btn deny" onclick="PermissionManager.makeDecision('${request.request_id}', 'deny')">
                    <i class="fas fa-times"></i> 拒绝
                </button>
                <button class="permission-btn allow-once" onclick="PermissionManager.makeDecision('${request.request_id}', 'allow_once')">
                    <i class="fas fa-check"></i> 本次
                </button>
                <button class="permission-btn allow-session" onclick="PermissionManager.makeDecision('${request.request_id}', 'allow_session')">
                    <i class="fas fa-clock"></i> 会话
                </button>
                <button class="permission-btn allow-always" onclick="PermissionManager.makeDecision('${request.request_id}', 'allow_always')">
                    <i class="fas fa-infinity"></i> 始终
                </button>
            </div>
            <div class="permission-timeout">
                <div class="permission-timeout-bar" style="animation-duration: ${request.timeout || 30}s"></div>
            </div>
        `;
        
        this.container.appendChild(toast);
        this.pendingRequests.set(request.request_id, request);
        
        // 超时自动拒绝
        setTimeout(() => {
            if (this.pendingRequests.has(request.request_id)) {
                this.makeDecision(request.request_id, 'deny');
            }
        }, (request.timeout || 30) * 1000);
        
        console.log('[PermissionManager] 显示权限请求:', request.request_id);
    },

    // 做出决策
    async makeDecision(requestId, decision) {
        console.log(`[PermissionManager] 决策: ${requestId} -> ${decision}`);
        
        try {
            // 通过WebSocket发送
            if (this.wsConnection?.readyState === WebSocket.OPEN) {
                this.wsConnection.send(JSON.stringify({
                    action: 'decide',
                    request_id: requestId,
                    decision: decision
                }));
            } else {
                // 通过HTTP发送
                await fetch(`${CONFIG.API_URL}/api/permission/decide`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ request_id: requestId, decision: decision })
                });
            }
            
            this.removeToast(requestId);
        } catch (e) {
            console.error('[PermissionManager] 决策失败:', e);
        }
    },

    // 移除弹窗
    removeToast(requestId) {
        const toast = document.getElementById(`permission-${requestId}`);
        if (toast) {
            toast.classList.add('hiding');
            setTimeout(() => toast.remove(), 300);
        }
        this.pendingRequests.delete(requestId);
    },

    // HTML转义
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    },

    // 创建权限请求（供模型调用）
    async createRequest(type, title, description, details) {
        try {
            const response = await fetch(`${CONFIG.API_URL}/api/permission/request`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    permission_type: type,
                    title: title,
                    description: description,
                    details: details,
                    session_id: currentSession || currentChatId
                })
            });
            const data = await response.json();
            return data;
        } catch (e) {
            console.error('[PermissionManager] 创建请求失败:', e);
            return { success: false };
        }
    }
};

// 初始化权限管理器
document.addEventListener('DOMContentLoaded', () => {
    setTimeout(() => PermissionManager.init(), 1000);
});

// ========== Dashboard 权限管理功能 ==========

// 权限类型图标映射
const PERM_ICONS = {
    internet: '🌐',
    web_browse: '🖥️',
    file_read: '📄',
    file_write: '✏️',
    code_exec: '💻',
    system_cmd: '⚙️',
    api_call: '🔌',
    dangerous: '⚠️'
};

// 加载权限规则列表
async function loadPermissionRules() {
    try {
        const response = await fetch(`${CONFIG.API_URL}/api/permission/rules`);
        const data = await response.json();
        
        const listEl = document.getElementById('permission-rules-list');
        if (!listEl) return;
        
        if (!data.success || data.rules.length === 0) {
            listEl.innerHTML = '<div class="permission-rule-empty">暂无权限规则</div>';
            return;
        }
        
        listEl.innerHTML = data.rules.map((rule, index) => `
            <div class="permission-rule-item">
                <div class="permission-rule-info">
                    <span class="permission-rule-type">${PERM_ICONS[rule.permission_type] || '🔐'}</span>
                    <span class="permission-rule-pattern">${escapeHtml(rule.pattern)}</span>
                    <span class="permission-rule-decision">${rule.decision === 'allow_always' ? '始终' : '会话'}</span>
                </div>
                <button class="permission-rule-delete" onclick="deletePermissionRule(${index})">
                    <i class="fas fa-times"></i>
                </button>
            </div>
        `).join('');
    } catch (e) {
        console.error('[loadPermissionRules] 加载失败:', e);
    }
}

// 添加权限规则
async function addPermissionRule() {
    const typeSelect = document.getElementById('perm-type-select');
    const patternInput = document.getElementById('perm-pattern-input');
    const decisionSelect = document.getElementById('perm-decision-select');
    
    if (!typeSelect || !patternInput || !decisionSelect) return;
    
    const permType = typeSelect.value;
    const pattern = patternInput.value.trim();
    const decision = decisionSelect.value;
    
    if (!pattern) {
        showNotification('请输入匹配模式', 'error');
        return;
    }
    
    try {
        // 通过创建请求并立即决策来添加规则
        const response = await fetch(`${CONFIG.API_URL}/api/permission/request`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                permission_type: permType,
                title: '添加权限规则',
                description: '从Dashboard添加',
                details: pattern,
                session_id: 'dashboard_rule'
            })
        });
        
        const data = await response.json();
        if (data.success) {
            // 立即做出决策
            await fetch(`${CONFIG.API_URL}/api/permission/decide`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    request_id: data.request_id,
                    decision: decision
                })
            });
            
            patternInput.value = '';
            await loadPermissionRules();
            showNotification('权限规则已添加', 'success');
        }
    } catch (e) {
        console.error('[addPermissionRule] 添加失败:', e);
        showNotification('添加失败', 'error');
    }
}

// 删除权限规则
async function deletePermissionRule(index) {
    try {
        const response = await fetch(`${CONFIG.API_URL}/api/permission/rules/${index}`, {
            method: 'DELETE'
        });
        
        const data = await response.json();
        if (data.success) {
            await loadPermissionRules();
            showNotification('规则已删除', 'success');
        }
    } catch (e) {
        console.error('[deletePermissionRule] 删除失败:', e);
        showNotification('删除失败', 'error');
    }
}

// 刷新待处理权限请求
async function refreshPendingPermissions() {
    try {
        const response = await fetch(`${CONFIG.API_URL}/api/permission/pending?session_id=${currentSession || currentChatId}`);
        const data = await response.json();
        
        const listEl = document.getElementById('pending-permissions-list');
        if (!listEl) return;
        
        if (!data.success || data.requests.length === 0) {
            listEl.innerHTML = '<div class="permission-pending-empty">暂无待处理请求</div>';
            return;
        }
        
        listEl.innerHTML = data.requests.map(req => `
            <div class="pending-permission-item">
                <div class="pending-permission-header">
                    <span>${PERM_ICONS[req.permission_type] || '🔐'}</span>
                    <span class="pending-permission-title">${escapeHtml(req.title)}</span>
                </div>
                <div class="pending-permission-details">${escapeHtml(req.details || '')}</div>
                <div class="pending-permission-actions">
                    <button class="pending-permission-btn deny" onclick="handlePendingPermission('${req.request_id}', 'deny')">
                        <i class="fas fa-times"></i> 拒绝
                    </button>
                    <button class="pending-permission-btn allow" onclick="handlePendingPermission('${req.request_id}', 'allow_once')">
                        <i class="fas fa-check"></i> 本次
                    </button>
                    <button class="pending-permission-btn allow" onclick="handlePendingPermission('${req.request_id}', 'allow_session')">
                        <i class="fas fa-clock"></i> 会话
                    </button>
                    <button class="pending-permission-btn allow" onclick="handlePendingPermission('${req.request_id}', 'allow_always')">
                        <i class="fas fa-infinity"></i> 始终
                    </button>
                </div>
            </div>
        `).join('');
    } catch (e) {
        console.error('[refreshPendingPermissions] 刷新失败:', e);
    }
}

// 处理待处理权限
async function handlePendingPermission(requestId, decision) {
    try {
        await fetch(`${CONFIG.API_URL}/api/permission/decide`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ request_id: requestId, decision: decision })
        });
        
        await refreshPendingPermissions();
        await loadPermissionRules();
        showNotification('已处理权限请求', 'success');
    } catch (e) {
        console.error('[handlePendingPermission] 处理失败:', e);
    }
}

// 清除所有权限规则
async function clearAllPermissionRules() {
    if (!confirm('确定要清除所有权限规则吗？')) return;
    
    try {
        // 获取所有规则并逐个删除
        const response = await fetch(`${CONFIG.API_URL}/api/permission/rules`);
        const data = await response.json();
        
        if (data.success && data.rules.length > 0) {
            // 从后往前删除
            for (let i = data.rules.length - 1; i >= 0; i--) {
                await fetch(`${CONFIG.API_URL}/api/permission/rules/${i}`, { method: 'DELETE' });
            }
        }
        
        await loadPermissionRules();
        showNotification('已清除所有规则', 'success');
    } catch (e) {
        console.error('[clearAllPermissionRules] 清除失败:', e);
        showNotification('清除失败', 'error');
    }
}

// 更新超时时间显示
function updatePermTimeoutDisplay() {
    const timeout = document.getElementById('perm-timeout');
    const display = document.getElementById('perm-timeout-value');
    if (timeout && display) {
        display.textContent = timeout.value;
    }
}

// 保存权限设置
function savePermissionSettings() {
    const settings = {
        autoDeny: document.getElementById('perm-auto-deny')?.checked || false,
        timeout: parseInt(document.getElementById('perm-timeout')?.value || 30),
        showToast: document.getElementById('perm-show-toast')?.checked || true,
        sound: document.getElementById('perm-sound')?.checked || false
    };
    
    localStorage.setItem('permission_settings', JSON.stringify(settings));
    console.log('[savePermissionSettings] 设置已保存:', settings);
}

// 加载权限设置
function loadPermissionSettings() {
    try {
        const saved = localStorage.getItem('permission_settings');
        if (!saved) return;
        
        const settings = JSON.parse(saved);
        
        const autoDeny = document.getElementById('perm-auto-deny');
        const timeout = document.getElementById('perm-timeout');
        const timeoutValue = document.getElementById('perm-timeout-value');
        const showToast = document.getElementById('perm-show-toast');
        const sound = document.getElementById('perm-sound');
        
        if (autoDeny) autoDeny.checked = settings.autoDeny;
        if (timeout) timeout.value = settings.timeout;
        if (timeoutValue) timeoutValue.textContent = settings.timeout;
        if (showToast) showToast.checked = settings.showToast;
        if (sound) sound.checked = settings.sound;
    } catch (e) {
        console.error('[loadPermissionSettings] 加载失败:', e);
    }
}

// HTML转义
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// 在Dashboard初始化时加载权限数据
const originalInitDashboard = typeof initDashboard === 'function' ? initDashboard : null;
function initDashboard() {
    if (originalInitDashboard) originalInitDashboard();
    
    // 加载权限数据
    loadPermissionRules();
    refreshPendingPermissions();
    loadPermissionSettings();
    updatePermissionStats();
    
    console.log('[initDashboard] 权限管理已初始化');
}

// 刷新所有权限数据
async function refreshAllPermissionData() {
    await Promise.all([
        loadPermissionRules(),
        refreshPendingPermissions(),
        updatePermissionStats()
    ]);
    showNotification('权限数据已刷新', 'success');
}

// 更新权限统计
async function updatePermissionStats() {
    try {
        // 获取规则数
        const rulesRes = await fetch(`${CONFIG.API_URL}/api/permission/rules`);
        const rulesData = await rulesRes.json();
        const rulesCount = rulesData.rules?.length || 0;
        
        // 获取待处理数
        const pendingRes = await fetch(`${CONFIG.API_URL}/api/permission/pending?session_id=${currentSession || currentChatId}`);
        const pendingData = await pendingRes.json();
        const pendingCount = pendingData.requests?.length || 0;
        
        // 计算会话规则数
        const sessionRules = rulesData.rules?.filter(r => r.decision === 'allow_session').length || 0;
        
        // 更新显示
        const rulesCountEl = document.getElementById('perm-rules-count');
        const pendingCountEl = document.getElementById('perm-pending-count');
        const sessionCountEl = document.getElementById('perm-session-count');
        const allowedCountEl = document.getElementById('perm-allowed-count');
        
        if (rulesCountEl) rulesCountEl.textContent = rulesCount;
        if (pendingCountEl) pendingCountEl.textContent = pendingCount;
        if (sessionCountEl) sessionCountEl.textContent = sessionRules;
        if (allowedCountEl) allowedCountEl.textContent = rulesCount - sessionRules;
        
    } catch (e) {
        console.error('[updatePermissionStats] 更新统计失败:', e);
    }
}

// 批准所有待处理请求
async function approveAllPending() {
    const data = await refreshPendingPermissions();
    if (data && data.requests && data.requests.length > 0) {
        for (const req of data.requests) {
            await handlePendingPermissionQuiet(req.request_id, 'allow_session');
        }
        await refreshAllPermissionData();
        showNotification(`已批准 ${data.requests.length} 个请求`, 'success');
    } else {
        showNotification('没有待处理的请求', 'info');
    }
}

// 拒绝所有待处理请求
async function denyAllPending() {
    const data = await refreshPendingPermissions();
    if (data && data.requests && data.requests.length > 0) {
        for (const req of data.requests) {
            await handlePendingPermissionQuiet(req.request_id, 'deny');
        }
        await refreshAllPermissionData();
        showNotification(`已拒绝 ${data.requests.length} 个请求`, 'warn');
    } else {
        showNotification('没有待处理的请求', 'info');
    }
}

// 静默处理权限（不显示通知）
async function handlePendingPermissionQuiet(requestId, decision) {
    try {
        await fetch(`${CONFIG.API_URL}/api/permission/decide`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ request_id: requestId, decision: decision })
        });
    } catch (e) {
        console.error('[handlePendingPermissionQuiet] 处理失败:', e);
    }
}

// ========== 每日任务调度器功能 ==========

let taskPanelOpen = false;
let taskDataCache = null;
let taskStatusBarCache = null;
let taskStatusBarLastAlert = '';
let taskHistoryPanelOpen = false;
let taskHistoryPanelState = null;
let taskHistoryPanelTaskId = '';
let cachedCurrentTaskId = '';
let taskStatusBarTreeExpanded = false;
let taskStatusBarTreeTaskId = '';
let taskStatusBarBodyExpanded = true;
let taskStatusBarBodyMinimized = false;
let taskStatusBarPopupMode = 'default';
let taskStatusBarCustomLeft = '';
let taskStatusBarCustomTop = '';
let taskStatusBarCustomWidth = '';
let taskStatusBarCustomHeight = '';
let taskStatusBarAlwaysOnTop = false;
let taskStatusBarEscHandler = null;

function loadCachedCurrentTaskId() {
    try {
        return localStorage.getItem('nanobot_current_task_id') || cachedCurrentTaskId || '';
    } catch (error) {
        return cachedCurrentTaskId || '';
    }
}

function cacheCurrentTaskId(taskId, source = '') {
    const normalized = String(taskId || '').trim();
    if (!normalized) return '';
    cachedCurrentTaskId = normalized;
    try {
        localStorage.setItem('nanobot_current_task_id', normalized);
    } catch (error) {
        console.warn('[cacheCurrentTaskId] Failed to persist task id cache:', source, error);
    }
    return normalized;
}

function getTaskStateMeta(state) {
    const normalized = String(state || '').trim().toLowerCase();
    const meta = {
        created: { label: '待创建', icon: 'fa-circle', className: 'task-state--created' },
        planned: { label: '已规划', icon: 'fa-clipboard-list', className: 'task-state--planned' },
        in_progress: { label: '进行中', icon: 'fa-spinner fa-spin', className: 'task-state--in_progress' },
        waiting_approval: { label: '待审批', icon: 'fa-hourglass-half', className: 'task-state--waiting_approval' },
        verifying: { label: '验证中', icon: 'fa-shield-alt fa-spin', className: 'task-state--verifying' },
        completed: { label: '已完成', icon: 'fa-check-circle', className: 'task-state--completed' },
        failed: { label: '失败', icon: 'fa-times-circle', className: 'task-state--failed' },
        blocked: { label: '已阻塞', icon: 'fa-ban', className: 'task-state--blocked' },
        cancelled: { label: '已取消', icon: 'fa-ban', className: 'task-state--cancelled' },
        loading: { label: '加载中', icon: 'fa-spinner fa-spin', className: 'task-state--loading' },
    };
    return meta[normalized] || { label: normalized || '未知', icon: 'fa-circle', className: 'task-state--unknown' };
}

function getTaskDisplayTitle(task = {}) {
    return String(task.title || task.objective || task.name || task.id || '未命名任务');
}

function getTaskTreeChildren(tasks = []) {
    return Array.isArray(tasks) ? tasks.filter(Boolean) : [];
}

async function copyTextToClipboard(text) {
    const value = String(text || '').trim();
    if (!value) return false;

    try {
        if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
            await navigator.clipboard.writeText(value);
            return true;
        }
    } catch (error) {
        console.warn('[copyTextToClipboard] navigator.clipboard failed:', error);
    }

    try {
        const textarea = document.createElement('textarea');
        textarea.value = value;
        textarea.setAttribute('readonly', 'readonly');
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        textarea.style.left = '-9999px';
        document.body.appendChild(textarea);
        textarea.select();
        textarea.setSelectionRange(0, value.length);
        const copied = document.execCommand('copy');
        document.body.removeChild(textarea);
        return copied;
    } catch (error) {
        console.warn('[copyTextToClipboard] execCommand fallback failed:', error);
        return false;
    }
}

function buildTaskTreeNodeHtml(task, { isRoot = false } = {}) {
    if (!task) return '';
    const state = String(task.state || task.status || '').trim().toLowerCase();
    const stateMeta = getTaskStateMeta(state || (isRoot ? 'loading' : 'created'));
    const title = getTaskDisplayTitle(task);
    const subtitle = task.current_step || task.objective || task.result_summary || '';
    const taskId = task.id || '';
    const isCompleted = state === 'completed';
    const isRunning = ['in_progress', 'verifying'].includes(state);
    const badgeIcon = isRunning ? '<i class="fas fa-spinner fa-spin"></i>' : (isCompleted ? '<i class="fas fa-check"></i>' : '<i class="fas fa-circle"></i>');
    const statusText = escapeHtml(stateMeta.label);
    const subtitleHtml = subtitle ? `<div class="task-status-bar__tree-subtitle">${escapeHtml(subtitle)}</div>` : '';
    const titleSuffix = isCompleted ? '<i class="fas fa-check task-status-bar__tree-check"></i>' : '';
    const nodeClass = isRoot ? 'task-status-bar__tree-node--root' : 'task-status-bar__tree-node--child';
    return `
        <div class="task-status-bar__tree-node ${nodeClass} ${stateMeta.className}">
            <div class="task-status-bar__tree-node-main">
                <span class="task-status-bar__tree-state-icon">${badgeIcon}</span>
                <div class="task-status-bar__tree-node-text">
                    <div class="task-status-bar__tree-node-title ${isRunning && !isRoot ? 'task-status-bar__tree-node-title--pulse' : ''}">
                        <span class="task-status-bar__tree-node-title-text">${escapeHtml(title)}</span>
                        ${titleSuffix}
                    </div>
                    ${subtitleHtml}
                </div>
                <span class="task-status-bar__tree-state-badge ${stateMeta.className}">${statusText}</span>
            </div>
            ${taskId ? `<div class="task-status-bar__tree-node-id">${escapeHtml(taskId)}</div>` : ''}
        </div>
    `;
}

async function preloadCurrentSessionSnapshot() {
    try {
        const response = await fetch(`${CONFIG.API_URL}/api/sessions/current`, {
            method: 'GET',
            headers: { 'Accept': 'application/json' },
        });
        if (!response.ok) return null;
        const snapshot = await response.json();
        if (snapshot && typeof snapshot === 'object') {
            if (snapshot.current_task_id) {
                cacheCurrentTaskId(snapshot.current_task_id, 'session_snapshot');
            }
            if (snapshot.task_update) {
                taskStatusBarCache = normalizeTaskUpdatePayload({
                    ...snapshot.task_update,
                    session_id: snapshot.current_session_id || snapshot.session_id || currentSession || currentChatId,
                });
            }
            if (snapshot.current_session_id && !currentSession) {
                currentSession = snapshot.current_session_id;
            }
        }
        return snapshot;
    } catch (error) {
        console.warn('[preloadCurrentSessionSnapshot] Failed to load snapshot:', error);
        return null;
    }
}

// 打开任务调度器面板
async function openTaskSchedulerPanel() {
    if (taskPanelOpen) return;
    
    // 创建面板
    const overlay = document.createElement('div');
    overlay.id = 'task-panel-overlay';
    overlay.className = 'task-panel-overlay';
    // 不再点击overlay关闭面板，允许背景可交互
    
    const panel = document.createElement('div');
    panel.className = 'task-panel';
    panel.innerHTML = `
        <div class="task-panel-header">
            <h3><i class="fas fa-tasks"></i> 每日任务调度中心</h3>
            <button class="task-panel-close" onclick="closeTaskSchedulerPanel()">
                <i class="fas fa-times"></i>
            </button>
        </div>
        <div class="task-panel-content" id="taskPanelContent">
            <div class="task-stats">
                <div class="task-stat-item">
                    <div class="task-stat-number" id="statTotal">-</div>
                    <div class="task-stat-label">总任务</div>
                </div>
                <div class="task-stat-item">
                    <div class="task-stat-number" id="statPending">-</div>
                    <div class="task-stat-label">待执行</div>
                </div>
                <div class="task-stat-item">
                    <div class="task-stat-number" id="statRunning" style="color:#3b82f6;">-</div>
                    <div class="task-stat-label">执行中</div>
                </div>
                <div class="task-stat-item">
                    <div class="task-stat-number" id="statCompleted" style="color:#10b981;">-</div>
                    <div class="task-stat-label">已完成</div>
                </div>
            </div>
            <div id="taskSections">
                <div class="task-empty">
                    <i class="fas fa-spinner fa-spin"></i>
                    <div>正在加载任务数据...</div>
                </div>
            </div>
        </div>
        <div id="taskSchedulerStatus"></div>
    `;
    
    overlay.appendChild(panel);
    document.body.appendChild(overlay);
    taskPanelOpen = true;
    
    // 启用拖拽功能
    makePanelDraggable(panel, '.task-panel-header');
    
    // 启用右键菜单
    makePanelContextMenu(panel);
    
    // 启用快捷键
    enablePanelShortcuts(panel, 'task');
    
    // 加载任务数据
    await loadTaskData();
}

// 关闭任务面板
function closeTaskSchedulerPanel() {
    const overlay = document.getElementById('task-panel-overlay');
    if (overlay) {
        overlay.remove();
        taskPanelOpen = false;
    }
}

// 加载任务数据
async function loadTaskData() {
    try {
        // 尝试从API获取任务数据
        const response = await fetch(`${CONFIG.API_URL}/api/tasks/status`, {
            method: 'GET',
            headers: { 'Content-Type': 'application/json' }
        }).catch(() => null);
        
        let data;
        if (response && response.ok) {
            data = await response.json();
        } else {
            // 使用本地存储的数据或模拟数据
            data = getLocalTaskData();
        }
        
        taskDataCache = data;
        renderTaskPanel(data);
        updateTaskBadge(data);
        
    } catch (error) {
        console.error('[loadTaskData] 加载任务数据失败:', error);
        renderTaskError('加载任务数据失败: ' + error.message);
    }
}

// 获取本地任务数据（当API不可用时）
function getLocalTaskData() {
    // 从localStorage获取上次保存的任务状态
    const saved = localStorage.getItem('nanobot_tasks_status');
    if (saved) {
        return JSON.parse(saved);
    }
    
    // 返回默认结构
    return {
        scheduler_running: false,
        tasks: [
            {
                id: 'task_health_check',
                name: '健康检查',
                command: 'python3 health_check.py check',
                status: 'pending',
                schedule_type: 'interval',
                interval_seconds: 1800,
                last_run: null,
                next_run: new Date(Date.now() + 30 * 60000).toISOString(),
                description: '每30分钟执行一次系统健康检查'
            },
            {
                id: 'task_cache_cleanup',
                name: '缓存清理',
                command: 'python3 smart_cache.py optimize',
                status: 'pending',
                schedule_type: 'interval',
                interval_seconds: 3600,
                last_run: null,
                next_run: new Date(Date.now() + 60 * 60000).toISOString(),
                description: '每小时清理和优化系统缓存'
            },
            {
                id: 'task_daily_backup',
                name: '每日备份',
                command: 'python3 backup_system.py create daily_backup',
                status: 'pending',
                schedule_type: 'daily',
                interval_seconds: 0,
                last_run: null,
                next_run: new Date(Date.now() + 24 * 60 * 60000).toISOString(),
                description: '每天执行系统数据备份'
            },
            {
                id: 'task_daily_reflection',
                name: '每日反思',
                command: 'python3 reflection.py reflect',
                status: 'pending',
                schedule_type: 'daily',
                interval_seconds: 0,
                last_run: null,
                next_run: new Date(Date.now() + 12 * 60 * 60000).toISOString(),
                description: '每日反思和记忆整理'
            },
            {
                id: 'task_memory_consolidation',
                name: '记忆整合',
                command: 'python3 memory_manager.py sleep',
                status: 'pending',
                schedule_type: 'daily',
                interval_seconds: 0,
                last_run: null,
                next_run: new Date(Date.now() + 18 * 60 * 60000).toISOString(),
                description: '整合和优化记忆存储'
            }
        ]
    };
}

function ensureTaskStatusBarStyles() {
    if (document.getElementById('taskStatusBarStyles')) return;
    const style = document.createElement('style');
    style.id = 'taskStatusBarStyles';
    style.textContent = `
        .task-status-bar {
            position: fixed;
            right:18px;
            bottom: 52px;
            left: auto;
            top: auto;
            z-index: 2147483000;
            margin: 0;
            padding: 5px 8px;
            border-radius: 10px;
            border: 1px solid rgba(148, 163, 184, 0.22);
            background: linear-gradient(180deg, rgba(15, 23, 42, 0.92) 0%, rgba(15, 23, 42, 0.82) 100%);
            backdrop-filter: blur(16px);
            box-shadow: 0 8px 18px rgba(15, 23, 42, 0.2);
            color: #e2e8f0;
            transition: all 180ms ease;
            width: min(360px, calc(100vw - 280px));
            max-width: 100%;
            overflow: visible;
        }
        .task-status-bar:hover { transform: translateY(-1px); }
        .task-status-bar--empty {
            opacity: 0.92;
        }
        .task-status-bar__header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
        }
        .task-status-bar__eyebrow {
            font-size: 8px;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            color: #94a3b8;
            margin-bottom: 1px;
        }
        .task-status-bar__title {
            font-size: 12px;
            font-weight: 700;
            color: #f8fafc;
            line-height: 1.15;
        }
        .task-status-bar__subtitle {
            margin-top: 1px;
            font-size: 10px;
            color: #cbd5e1;
            line-height: 1.2;
        }
        .task-status-bar__status-pill,
        .task-status-bar__verify-pill {
            display: inline-flex;
            align-items: center;
            gap: 4px;
            padding: 3px 6px;
            border-radius: 999px;
            font-size: 10px;
            font-weight: 700;
            white-space: nowrap;
            line-height: 1;
        }
        .task-status-bar__status-pill--created { background: rgba(148, 163, 184, 0.18); color: #cbd5e1; }
        .task-status-bar__status-pill--planned { background: rgba(59, 130, 246, 0.18); color: #93c5fd; }
        .task-status-bar__status-pill--in_progress { background: rgba(14, 165, 233, 0.18); color: #67e8f9; }
        .task-status-bar__status-pill--verifying { background: rgba(168, 85, 247, 0.18); color: #d8b4fe; }
        .task-status-bar__status-pill--waiting_approval { background: rgba(245, 158, 11, 0.22); color: #fcd34d; }
        .task-status-bar__status-pill--completed { background: rgba(16, 185, 129, 0.2); color: #86efac; }
        .task-status-bar__status-pill--failed,
        .task-status-bar__status-pill--blocked { background: rgba(239, 68, 68, 0.22); color: #fca5a5; }
        .task-status-bar__status-pill--cancelled { background: rgba(100, 116, 139, 0.22); color: #cbd5e1; }
        .task-status-bar__body {
            margin-top: 6px;
            display: grid;
            gap: 8px;
            overflow: hidden;
            transition: max-height 250ms ease, opacity 200ms ease, margin-top 200ms ease;
        }
        .task-status-bar__body.task-status-bar__body--expanded {
            max-height: 2000px;
            opacity: 1;
            margin-top: 12px;
        }
        .task-status-bar__body.task-status-bar__body--collapsed {
            max-height: 0;
            opacity: 0;
            margin-top: 0;
            pointer-events: none;
        }
        .task-status-bar__header {
            cursor: pointer;
            user-select: none;
        }
        .task-status-bar__header.is-dragging {
            cursor: grabbing;
        }
        .task-status-bar__header:hover .task-status-bar__title {
            text-decoration: underline;
            text-decoration-style: dotted;
            text-underline-offset: 3px;
        }
        .task-status-bar__collapse-hint {
            font-size: 9px;
            color: #64748b;
            margin-left: 4px;
        }
        .task-status-bar__title-wrap {
            min-width: 0;
            flex: 1 1 auto;
        }
        .task-status-bar__title,
        .task-status-bar__subtitle {
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .task-status-bar__body.task-status-bar__body--expanded {
            position: fixed;
            width: min(520px, calc(100vw - 24px));
            max-height: min(70vh, 720px);
            padding: 10px 12px;
            border-radius: 14px;
            border: 1px solid rgba(148, 163, 184, 0.18);
            background: linear-gradient(180deg, rgba(15, 23, 42, 0.98) 0%, rgba(15, 23, 42, 0.94) 100%);
            box-shadow: 0 18px 42px rgba(15, 23, 42, 0.34);
            overflow: auto;
            z-index: 2147483001;
        }
        .task-status-bar__meta {
            display: flex;
            flex-wrap: wrap;
            gap: 10px 14px;
            font-size: 12px;
            color: #94a3b8;
        }
        .task-status-bar__meta strong { color: #e2e8f0; }
        .task-status-bar__close-btn {
            margin-left: auto;
            padding: 4px 8px;
            border-radius: 8px;
            border: 1px solid rgba(148, 163, 184, 0.22);
            background: rgba(15, 23, 42, 0.24);
            color: #94a3b8;
            cursor: pointer;
            transition: all 160ms ease;
            display: inline-flex;
            align-items: center;
            justify-content: center;
        }
        .task-status-bar__close-btn:hover {
            color: #f8fafc;
            border-color: rgba(239, 68, 68, 0.42);
            background: rgba(239, 68, 68, 0.18);
            transform: translateY(-1px);
        }
        .task-status-bar__minimize-btn {
            padding: 4px 8px;
            border-radius: 8px;
            border: 1px solid rgba(148, 163, 184, 0.22);
            background: rgba(15, 23, 42, 0.24);
            color: #94a3b8;
            cursor: pointer;
            transition: all 160ms ease;
            display: inline-flex;
            align-items: center;
            justify-content: center;
        }
        .task-status-bar__minimize-btn:hover {
            color: #f8fafc;
            border-color: rgba(59, 130, 246, 0.42);
            background: rgba(59, 130, 246, 0.18);
            transform: translateY(-1px);
        }
        .task-status-bar__body.task-status-bar__body--minimized {
            max-height: 0;
            opacity: 0;
            overflow: hidden;
            padding: 0;
        }
        .task-status-bar__resize-handle {
            position: absolute;
            right: 0;
            bottom: 0;
            width: 18px;
            height: 18px;
            cursor: se-resize;
            background: linear-gradient(135deg, transparent 50%, rgba(99, 102, 241, 0.55) 50%);
            border-radius: 0 0 14px 0;
            transition: opacity 160ms ease;
        }
        .task-status-bar__resize-handle:hover {
            background: linear-gradient(135deg, transparent 50%, rgba(99, 102, 241, 0.8) 50%);
        }
        .task-status-bar__task-link {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 4px 8px;
            border-radius: 10px;
            border: 1px solid rgba(148, 163, 184, 0.22);
            background: rgba(15, 23, 42, 0.24);
            color: #cbd5e1;
            cursor: pointer;
            transition: all 160ms ease;
        }
        .task-status-bar__task-link:hover {
            color: #f8fafc;
            border-color: rgba(56, 189, 248, 0.42);
            background: rgba(14, 165, 233, 0.18);
            transform: translateY(-1px);
        }
        .task-status-bar__task-link i {
            color: #38bdf8;
            font-size: 11px;
        }
        .task-status-bar__progress-track {
            width: 100%;
            height: 10px;
            border-radius: 999px;
            background: rgba(148, 163, 184, 0.14);
            overflow: hidden;
        }
        .task-status-bar__progress-fill {
            height: 100%;
            border-radius: inherit;
            background: linear-gradient(90deg, #38bdf8 0%, #8b5cf6 100%);
            transition: width 240ms ease;
        }
        .task-status-bar__progress-line {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 12px;
            font-size: 12px;
            color: #cbd5e1;
        }
        .task-status-bar__verify-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
        }
        .task-status-bar__verify-pill--PASS { background: rgba(16, 185, 129, 0.2); color: #86efac; }
        .task-status-bar__verify-pill--FAIL { background: rgba(239, 68, 68, 0.22); color: #fca5a5; }
        .task-status-bar__verify-pill--PARTIAL { background: rgba(245, 158, 11, 0.22); color: #fcd34d; }
        .task-status-bar__verify-pill--PENDING { background: rgba(59, 130, 246, 0.18); color: #93c5fd; }
        .task-status-bar__alert {
            margin-top: 12px;
            padding: 12px 14px;
            border-radius: 12px;
            border: 1px solid rgba(248, 113, 113, 0.28);
            background: linear-gradient(135deg, rgba(127, 29, 29, 0.38) 0%, rgba(91, 33, 182, 0.22) 100%);
            color: #fecaca;
            font-size: 13px;
            line-height: 1.5;
        }
        .task-status-bar--warning {
            border-color: rgba(245, 158, 11, 0.42);
            box-shadow: 0 0 0 1px rgba(245, 158, 11, 0.12), 0 16px 40px rgba(15, 23, 42, 0.34);
        }
        .task-status-bar--blocked,
        .task-status-bar--failed {
            border-color: rgba(248, 113, 113, 0.42);
            box-shadow: 0 0 0 1px rgba(248, 113, 113, 0.14), 0 16px 40px rgba(15, 23, 42, 0.34);
        }
        .task-status-bar__empty-state {
            display: flex;
            align-items: center;
            gap: 10px;
            color: #cbd5e1;
        }
        .task-status-bar__empty-state i { color: #38bdf8; }
        .task-history-modal {
            position: fixed;
            inset: 0;
            z-index: 2147483648;
            display: none;
            background: rgba(2, 6, 23, 0.58);
            backdrop-filter: blur(8px);
        }
        .task-history-modal.active { display: block; }
        .task-history-modal__panel {
            position: absolute;
            right: 0;
            top: 0;
            width: min(560px, 100vw);
            height: 100%;
            background: linear-gradient(180deg, rgba(15, 23, 42, 0.98) 0%, rgba(15, 23, 42, 0.94) 100%);
            border-left: 1px solid rgba(148, 163, 184, 0.18);
            box-shadow: -24px 0 60px rgba(2, 6, 23, 0.38);
            display: flex;
            flex-direction: column;
        }
        .task-history-modal__header {
            padding: 18px 18px 14px;
            border-bottom: 1px solid rgba(148, 163, 184, 0.14);
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 14px;
        }
        .task-history-modal__title {
            display: flex;
            flex-direction: column;
            gap: 4px;
        }
        .task-history-modal__title strong {
            color: #f8fafc;
            font-size: 16px;
        }
        .task-history-modal__title span {
            color: #94a3b8;
            font-size: 12px;
        }
        .task-history-modal__close {
            border: 1px solid rgba(148, 163, 184, 0.2);
            background: rgba(15, 23, 42, 0.3);
            color: #e2e8f0;
            width: 36px;
            height: 36px;
            border-radius: 10px;
            cursor: pointer;
        }
        .task-history-modal__body {
            flex: 1;
            overflow: auto;
            padding: 16px 18px 20px;
        }
        .task-history-modal__footer {
            border-top: 1px solid rgba(148, 163, 184, 0.14);
            padding: 14px 18px 18px;
            display: none;
        }
        .task-history-modal__manual-transition {
            display: grid;
            gap: 10px;
        }
        .task-history-modal__manual-title {
            color: #f8fafc;
            font-size: 13px;
            font-weight: 700;
        }
        .task-history-modal__manual-form {
            display: grid;
            grid-template-columns: minmax(160px, 180px) minmax(0, 1fr) auto;
            gap: 8px;
            align-items: center;
        }
        .task-history-modal__manual-state,
        .task-history-modal__manual-reason,
        .task-history-modal__manual-submit {
            border-radius: 10px;
            border: 1px solid rgba(148, 163, 184, 0.18);
            background: rgba(15, 23, 42, 0.5);
            color: #e2e8f0;
            padding: 10px 12px;
            font-size: 13px;
        }
        .task-history-modal__manual-state:focus,
        .task-history-modal__manual-reason:focus {
            outline: 2px solid rgba(96, 165, 250, 0.8);
            outline-offset: 2px;
        }
        .task-history-modal__manual-submit {
            cursor: pointer;
            font-weight: 700;
            background: linear-gradient(180deg, rgba(59, 130, 246, 0.9), rgba(37, 99, 235, 0.9));
        }
        .task-history-modal__manual-submit:disabled {
            opacity: 0.7;
            cursor: not-allowed;
        }
        .task-history-modal__manual-hint {
            color: #94a3b8;
            font-size: 12px;
        }
        .task-history-modal__loading,
        .task-history-modal__empty,
        .task-history-modal__error {
            color: #cbd5e1;
            padding: 18px;
            border: 1px dashed rgba(148, 163, 184, 0.22);
            border-radius: 14px;
            background: rgba(15, 23, 42, 0.28);
        }
        .task-history-log {
            display: grid;
            gap: 10px;
        }
        .task-history-entry {
            padding: 12px 14px;
            border-radius: 14px;
            border: 1px solid rgba(148, 163, 184, 0.14);
            background: rgba(15, 23, 42, 0.36);
        }
        .task-history-entry__top {
            display: flex;
            justify-content: space-between;
            gap: 10px;
            align-items: flex-start;
            margin-bottom: 8px;
        }
        .task-history-entry__states {
            display: flex;
            align-items: center;
            flex-wrap: wrap;
            gap: 8px;
            color: #e2e8f0;
            font-size: 13px;
        }
        .task-history-entry__states code {
            padding: 3px 6px;
            border-radius: 8px;
            background: rgba(56, 189, 248, 0.14);
            color: #93c5fd;
        }
        .task-history-entry__meta {
            color: #94a3b8;
            font-size: 12px;
            display: flex;
            flex-wrap: wrap;
            gap: 8px 10px;
        }
        .task-history-entry__reason {
            margin-top: 8px;
            color: #cbd5e1;
            font-size: 13px;
            line-height: 1.5;
            white-space: pre-wrap;
            word-break: break-word;
        }
        .task-history-entry__source {
            color: #67e8f9;
            font-size: 12px;
        }
        .task-status-bar__tree {
            display: grid;
            gap: 10px;
        }
        .task-status-bar__tree-panel {
            display: grid;
            gap: 8px;
        }
        .task-status-bar__tree-panel-actions {
            display: flex;
            justify-content: flex-end;
            align-items: center;
            gap: 8px;
        }
        .task-status-bar__copy-task-id {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            border: 1px solid rgba(148, 163, 184, 0.18);
            background: rgba(15, 23, 42, 0.52);
            color: #e2e8f0;
            border-radius: 999px;
            padding: 5px 10px;
            font-size: 12px;
            line-height: 1;
            cursor: pointer;
            transition: transform 0.15s ease, border-color 0.15s ease, background 0.15s ease, box-shadow 0.15s ease;
        }
        .task-status-bar__copy-task-id i {
            font-size: 11px;
        }
        .task-status-bar__copy-task-id:hover {
            border-color: rgba(96, 165, 250, 0.6);
            background: rgba(30, 41, 59, 0.78);
            box-shadow: 0 8px 18px rgba(15, 23, 42, 0.2);
            transform: translateY(-1px);
        }
        .task-status-bar__copy-task-id:focus-visible {
            outline: 2px solid rgba(96, 165, 250, 0.85);
            outline-offset: 2px;
        }
        .task-status-bar__copy-task-id:disabled,
        .task-status-bar__copy-task-id[aria-disabled="true"] {
            opacity: 0.55;
            cursor: not-allowed;
            box-shadow: none;
            transform: none;
        }
        .task-status-bar__tree-root,
        .task-status-bar__tree-toggle,
        .task-status-bar__task-link {
            border: 0;
            background: none;
        }
        .task-status-bar__tree-root {
            width: 100%;
            display: flex;
            align-items: flex-start;
            gap: 12px;
            padding: 14px 16px;
            border-radius: 16px;
            background: rgba(15, 23, 42, 0.44);
            border: 1px solid rgba(148, 163, 184, 0.18);
            color: #e2e8f0;
            cursor: pointer;
            text-align: left;
        }
        .task-status-bar__tree-root:hover {
            border-color: rgba(96, 165, 250, 0.45);
            box-shadow: 0 10px 28px rgba(15, 23, 42, 0.22);
        }
        .task-status-bar__tree-root:focus-visible,
        .task-status-bar__tree-toggle:focus-visible,
        .task-status-bar__task-link:focus-visible {
            outline: 2px solid rgba(96, 165, 250, 0.85);
            outline-offset: 2px;
        }
        .task-status-bar__tree-toggle-icon {
            width: 24px;
            min-width: 24px;
            height: 24px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border-radius: 8px;
            background: rgba(59, 130, 246, 0.12);
            color: #93c5fd;
            margin-top: 2px;
        }
        .task-status-bar__tree-root-main {
            flex: 1;
            display: grid;
            gap: 6px;
        }
        .task-status-bar__tree-root-title {
            display: flex;
            align-items: center;
            gap: 10px;
            justify-content: space-between;
            flex-wrap: wrap;
        }
        .task-status-bar__tree-root-title-text {
            font-size: 15px;
            font-weight: 700;
            color: #f8fafc;
        }
        .task-status-bar__tree-root-id {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            color: #94a3b8;
            font-size: 12px;
        }
        .task-status-bar__tree-root-summary {
            color: #cbd5e1;
            font-size: 13px;
            line-height: 1.45;
        }
        .task-status-bar__tree-root-meta {
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }
        .task-status-bar__tree-toggle {
            flex-shrink: 0;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            color: #bfdbfe;
            padding: 6px 10px;
            border-radius: 999px;
            background: rgba(59, 130, 246, 0.12);
            cursor: pointer;
            font-size: 12px;
        }
        .task-status-bar__tree-toggle--disabled {
            opacity: 0.7;
            cursor: default;
        }
        .task-status-bar__tree-children {
            display: grid;
            gap: 8px;
            padding-left: 16px;
            border-left: 2px solid rgba(96, 165, 250, 0.24);
            margin-left: 4px;
        }
        .task-status-bar__tree-node {
            position: relative;
            padding: 10px 12px;
            border-radius: 14px;
            border: 1px solid rgba(148, 163, 184, 0.12);
            background: rgba(15, 23, 42, 0.32);
        }
        .task-status-bar__tree-node--root {
            background: rgba(2, 6, 23, 0.3);
        }
        .task-status-bar__tree-node--child {
            padding-left: 18px;
        }
        .task-status-bar__tree-node--child::before {
            content: '';
            position: absolute;
            left: 0;
            top: 10px;
            bottom: 10px;
            width: 4px;
            border-radius: 999px;
            background: rgba(96, 165, 250, 0.5);
        }
        .task-status-bar__tree-node--child.task-state--in_progress::before {
            background: #3b82f6;
        }
        .task-status-bar__tree-node--child.task-state--completed::before {
            background: #22c55e;
        }
        .task-status-bar__tree-node--child.task-state--failed::before {
            background: #ef4444;
        }
        .task-status-bar__tree-node--child.task-state--other::before {
            background: #64748b;
        }
        .task-status-bar__tree-node-main {
            display: flex;
            align-items: flex-start;
            gap: 10px;
        }
        .task-status-bar__tree-state-icon {
            width: 18px;
            min-width: 18px;
            margin-top: 3px;
            color: #93c5fd;
        }
        .task-status-bar__tree-node-text {
            flex: 1;
            min-width: 0;
        }
        .task-status-bar__tree-node-title {
            display: flex;
            align-items: center;
            gap: 8px;
            color: #f8fafc;
            font-size: 13px;
            font-weight: 600;
            line-height: 1.45;
            flex-wrap: wrap;
        }
        .task-status-bar__tree-node-title--pulse {
            animation: taskTreeTitlePulse 1.35s ease-in-out infinite;
        }
        .task-status-bar__tree-node-title-text {
            word-break: break-word;
        }
        .task-status-bar__tree-check {
            color: #22c55e;
            font-size: 12px;
        }
        .task-status-bar__tree-subtitle {
            margin-top: 4px;
            color: #cbd5e1;
            font-size: 12px;
            line-height: 1.45;
        }
        .task-status-bar__tree-node-id {
            margin-top: 8px;
            color: #94a3b8;
            font-size: 11px;
            word-break: break-all;
            opacity: 0.9;
        }
        .task-status-bar__tree-state-badge {
            flex-shrink: 0;
            padding: 4px 8px;
            border-radius: 999px;
            font-size: 11px;
            line-height: 1;
            border: 1px solid rgba(148, 163, 184, 0.16);
            background: rgba(15, 23, 42, 0.45);
        }
        .task-status-bar__tree-node.task-state--completed .task-status-bar__tree-state-badge,
        .task-status-bar__tree-node.task-state--completed .task-status-bar__tree-toggle-icon {
            color: #86efac;
        }
        .task-status-bar__tree-node.task-state--in_progress .task-status-bar__tree-state-badge,
        .task-status-bar__tree-node.task-state--verifying .task-status-bar__tree-state-badge {
            color: #93c5fd;
        }
        .task-status-bar__tree-node.task-state--blocked .task-status-bar__tree-state-badge,
        .task-status-bar__tree-node.task-state--failed .task-status-bar__tree-state-badge {
            color: #fca5a5;
        }
        .task-status-bar__tree-node.task-state--planned .task-status-bar__tree-state-badge {
            color: #c4b5fd;
        }
        .task-status-bar__tree-node.task-state--loading .task-status-bar__tree-state-badge {
            color: #fbbf24;
        }
        .task-status-bar__tree-node--child.task-state--in_progress .task-status-bar__tree-node-title-text {
            color: #bfdbfe;
        }
        .task-status-bar__tree-node--child.task-state--in_progress .task-status-bar__tree-state-icon {
            color: #60a5fa;
        }
        .task-status-bar__tree-node--child.task-state--in_progress::before,
        .task-status-bar__tree-node--child.task-state--verifying::before {
            background: linear-gradient(180deg, #60a5fa 0%, #3b82f6 100%);
            box-shadow: 0 0 0 1px rgba(59, 130, 246, 0.18);
        }
        .task-status-bar__tree-node--child.task-state--completed::before {
            background: linear-gradient(180deg, #86efac 0%, #22c55e 100%);
            box-shadow: 0 0 0 1px rgba(34, 197, 94, 0.18);
        }
        .task-status-bar__tree-node--child.task-state--blocked::before,
        .task-status-bar__tree-node--child.task-state--failed::before {
            background: linear-gradient(180deg, #fca5a5 0%, #ef4444 100%);
            box-shadow: 0 0 0 1px rgba(239, 68, 68, 0.18);
        }
        .task-status-bar__tree-node--child.task-state--planned::before {
            background: linear-gradient(180deg, #d8b4fe 0%, #a855f7 100%);
            box-shadow: 0 0 0 1px rgba(168, 85, 247, 0.16);
        }
        .task-status-bar__tree-node--child.task-state--created::before,
        .task-status-bar__tree-node--child.task-state--loading::before {
            background: linear-gradient(180deg, #fde68a 0%, #f59e0b 100%);
            box-shadow: 0 0 0 1px rgba(245, 158, 11, 0.16);
        }
        .task-status-bar__tree-root-actions {
            position: absolute;
            top: 10px;
            right: 10px;
            display: flex;
            gap: 8px;
            z-index: 2;
        }
        .task-status-bar__copy-root-task {
            width: 28px;
            height: 28px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border-radius: 999px;
            border: 1px solid rgba(148, 163, 184, 0.18);
            background: rgba(15, 23, 42, 0.72);
            color: #e2e8f0;
            cursor: pointer;
            position: relative;
        }
        .task-status-bar__copy-root-task:hover {
            border-color: rgba(96, 165, 250, 0.65);
            color: #93c5fd;
        }
        .task-status-bar__copy-root-task.is-copied::after {
            content: '已复制';
            position: absolute;
            top: -30px;
            right: 0;
            background: rgba(15, 23, 42, 0.95);
            border: 1px solid rgba(148, 163, 184, 0.25);
            color: #f8fafc;
            border-radius: 8px;
            padding: 4px 8px;
            font-size: 12px;
            white-space: nowrap;
            box-shadow: 0 10px 22px rgba(15, 23, 42, 0.28);
        }
        @keyframes taskTreeSpin {
            from {
                transform: rotate(0deg);
            }
            to {
                transform: rotate(360deg);
            }
        }
        @keyframes taskTreeNodePulse {
            0%, 100% {
                transform: scale(1);
                filter: saturate(1);
            }
            50% {
                transform: scale(1.06);
                filter: saturate(1.25);
            }
        }
        .task-status-bar__tree-node--child.task-state--in_progress .task-status-bar__tree-state-icon,
        .task-status-bar__tree-node--child.task-state--verifying .task-status-bar__tree-state-icon {
            color: #60a5fa;
            animation: taskTreeNodePulse 1.4s ease-in-out infinite;
        }
        .task-status-bar__tree-node--child.task-state--in_progress .task-status-bar__tree-state-icon i,
        .task-status-bar__tree-node--child.task-state--verifying .task-status-bar__tree-state-icon i {
            animation: taskTreeSpin 1s linear infinite;
        }
        @keyframes taskTreeTitlePulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.72; }
        }
        @media (max-width: 768px) {
            .task-history-modal__panel { width: 100vw; }
            .task-history-modal__manual-form { grid-template-columns: 1fr; }
        }
        @media (max-width: 920px) {
            .task-status-bar__header, .task-status-bar__verify-row { flex-direction: column; align-items: flex-start; }
            .task-status-bar__tree-root { padding: 12px; }
            .task-status-bar__tree-children { padding-left: 10px; margin-left: 2px; }
            .task-status-bar__body.task-status-bar__body--expanded {
                width: min(92vw, 520px);
            }
        }
    `;
    document.head.appendChild(style);
}

function ensureTaskStatusBar() {
    ensureTaskStatusBarStyles();
    let host = document.getElementById('taskStatusBar');
    if (host) {
        if (host.parentNode !== document.body) {
            document.body.appendChild(host);
        }
        return host;
    }

    host = document.createElement('section');
    host.id = 'taskStatusBar';
    host.className = 'task-status-bar task-status-bar--empty';
    host.dataset.defaultWidth = '360px';
    host.dataset.defaultHeight = '';
    host.dataset.defaultLeft = 'auto';
    host.dataset.defaultTop = 'auto';
    host.dataset.defaultRight = '80px';
    host.dataset.defaultBottom = '80px';
    host.dataset.defaultTransform = 'none';
    host.dataset.defaultMaxWidth = 'min(360px, calc(100vw - 280px))';
    host.dataset.defaultMaxHeight = '';
    host.innerHTML = `
        <div class="task-status-bar__empty-state">
            <i class="fas fa-layer-group"></i>
            <span>任务状态栏已准备就绪，等待当前会话的任务更新。</span>
        </div>
    `;

    document.body.appendChild(host);
    makePanelContextMenu(host);
    enablePanelShortcuts(host, 'task_status_bar');
    return host;
}

function bindTaskStatusBarHeaderDrag(host, header) {
    if (!host || !header) return;
    header.onpointerdown = (event) => {
        if (event.button !== 0) return;
        if (event.target?.closest?.('button, a, input, textarea, select, .task-status-bar__status-pill')) return;
        const rect = host.getBoundingClientRect();
        const offsetX = event.clientX - rect.left;
        const offsetY = event.clientY - rect.top;
        let moved = false;
        host.dataset.dragSuppressClick = 'false';
        activateShortcutPanel(host);

        const moveHandler = (moveEvent) => {
            const nextLeft = Math.max(8, Math.min(moveEvent.clientX - offsetX, window.innerWidth - rect.width - 8));
            const nextTop = Math.max(8, Math.min(moveEvent.clientY - offsetY, window.innerHeight - rect.height - 8));
            if (Math.abs(nextLeft - rect.left) > 3 || Math.abs(nextTop - rect.top) > 3) {
                if (!moved) {
                    moved = true;
                    host.dataset.dragSuppressClick = 'true';
                    header.classList.add('is-dragging');
                    host.setPointerCapture?.(event.pointerId);
                }
            }
            if (!moved) return;
            host.style.left = `${nextLeft}px`;
            host.style.top = `${nextTop}px`;
            host.style.right = 'auto';
            host.style.bottom = 'auto';
            host.dataset.userPositioned = 'true';
            positionTaskStatusBarBody(host);
        };

        const upHandler = (upEvent) => {
            header.classList.remove('is-dragging');
            if (moved) {
                host.releasePointerCapture?.(upEvent.pointerId);
            }
            document.removeEventListener('pointermove', moveHandler);
            document.removeEventListener('pointerup', upHandler);
            document.removeEventListener('pointercancel', upHandler);
            if (moved) {
                window.setTimeout(() => {
                    host.dataset.dragSuppressClick = 'false';
                }, 0);
            } else {
                // Normal click (no drag) — toggle expand/collapse.
                // This is done here (document-level pointerup) instead of
                // header.onclick because if an SSE task_update re-renders
                // the host between pointerdown and click, the old header
                // is destroyed and the click event may not fire on it.
                // Document-level pointerup is immune to DOM replacement.
                host.dataset.dragSuppressClick = 'false';
                activateShortcutPanel(host);
                taskStatusBarBodyExpanded = !taskStatusBarBodyExpanded;
                if (taskStatusBarBodyExpanded) {
                    taskStatusBarBodyMinimized = false;
                }
                if (!taskStatusBarBodyExpanded) {
                    taskStatusBarPopupMode = 'default';
                    taskStatusBarCustomLeft = '';
                    taskStatusBarCustomTop = '';
                    taskStatusBarCustomWidth = '';
                    taskStatusBarCustomHeight = '';
                }
                console.log('[taskStatusBar] pointerup toggle →', taskStatusBarBodyExpanded ? 'EXPAND' : 'COLLAPSE');
                const payload = taskStatusBarCache || loadTaskStatusBarCache(currentSession || currentChatId) || { root_task: {}, child_tasks: [] };
                renderTaskStatusBar(payload, { emptyMessage: '当前会话任务已更新。' });
            }
        };

        document.addEventListener('pointermove', moveHandler);
        document.addEventListener('pointerup', upHandler);
        document.addEventListener('pointercancel', upHandler);
    };
}

function bindTaskStatusBarBodyDrag(host, body) {
    if (!host || !body) return;
    const DRAG_EXCLUDE = 'button, a, input, textarea, select, ' +
        '.task-status-bar__tree-node, .task-status-bar__tree-children, ' +
        '.task-status-bar__task-link, .task-status-bar__resize-handle';

    body.onpointerdown = (event) => {
        if (event.button !== 0) return;
        if (event.target?.closest?.(DRAG_EXCLUDE)) return;

        // Capture visual position BEFORE any DOM changes
        const rect = body.getBoundingClientRect();

        // Portal body to document.body to escape host's backdrop-filter
        // containing block (backdrop-filter makes position:fixed relative to host)
        if (body.parentNode !== document.body) {
            body.style.transition = 'none';
            body.style.left = `${rect.left}px`;
            body.style.top = `${rect.top}px`;
            body.style.right = 'auto';
            body.style.bottom = 'auto';
            body.style.transform = 'none';
            document.body.appendChild(body);
        }
        body.style.transition = 'none';

        const offsetX = event.clientX - rect.left;
        const offsetY = event.clientY - rect.top;
        let moved = false;
        host.dataset.dragSuppressClick = 'false';
        activateShortcutPanel(host);

        const moveHandler = (moveEvent) => {
            let nextLeft = moveEvent.clientX - offsetX;
            let nextTop = moveEvent.clientY - offsetY;
            // Keep at least 40px of panel visible on each edge
            nextLeft = Math.max(-rect.width + 40, Math.min(nextLeft, window.innerWidth - 40));
            nextTop = Math.max(0, Math.min(nextTop, window.innerHeight - 40));
            if (!moved) {
                if (Math.abs(nextLeft - rect.left) > 3 || Math.abs(nextTop - rect.top) > 3) {
                    moved = true;
                    host.dataset.dragSuppressClick = 'true';
                    body.classList.add('is-dragging');
                    body.setPointerCapture?.(event.pointerId);
                }
            }
            if (!moved) return;
            body.style.left = `${nextLeft}px`;
            body.style.top = `${nextTop}px`;
        };

        const upHandler = (upEvent) => {
            body.classList.remove('is-dragging');
            body.style.transition = '';
            if (moved) {
                body.releasePointerCapture?.(upEvent.pointerId);
                taskStatusBarPopupMode = 'custom';
                taskStatusBarCustomLeft = body.style.left;
                taskStatusBarCustomTop = body.style.top;
                body.dataset.taskStatusBarPopupMode = 'custom';
                body.dataset.taskStatusBarPopupLeft = body.style.left;
                body.dataset.taskStatusBarPopupTop = body.style.top;
            }
            document.removeEventListener('pointermove', moveHandler);
            document.removeEventListener('pointerup', upHandler);
            document.removeEventListener('pointercancel', upHandler);
            if (moved) {
                window.setTimeout(() => {
                    host.dataset.dragSuppressClick = 'false';
                }, 0);
            }
        };

        document.addEventListener('pointermove', moveHandler);
        document.addEventListener('pointerup', upHandler);
        document.addEventListener('pointercancel', upHandler);
    };
}

function getTaskStatusBarStorageKey(sessionId) {
    return `nanobot_task_status_bar_${sessionId || 'default'}`;
}

function cloneTaskList(tasks) {
    return Array.isArray(tasks) ? tasks.filter(Boolean).map(task => ({ ...task })) : [];
}

function normalizeTaskUpdatePayload(data = {}) {
    const rootTask = data.root_task || data.task || {};
    const childTasks = cloneTaskList(data.child_tasks || data.tasks || []);
    const safeRoot = rootTask && typeof rootTask === 'object' ? { ...rootTask } : {};

    const normalizeState = (task) => String(task?.state || task?.status || '').trim().toLowerCase();
    const counts = data.counts && typeof data.counts === 'object' ? { ...data.counts } : {
        total: childTasks.length,
        created: childTasks.filter(task => normalizeState(task) === 'created').length,
        planned: childTasks.filter(task => normalizeState(task) === 'planned').length,
        in_progress: childTasks.filter(task => normalizeState(task) === 'in_progress').length,
        waiting_approval: childTasks.filter(task => normalizeState(task) === 'waiting_approval').length,
        verifying: childTasks.filter(task => normalizeState(task) === 'verifying').length,
        completed: childTasks.filter(task => normalizeState(task) === 'completed').length,
        failed: childTasks.filter(task => ['failed', 'blocked', 'cancelled'].includes(normalizeState(task))).length,
    };

    const progress = data.progress && typeof data.progress === 'object' ? { ...data.progress } : {
        completed: counts.completed || 0,
        total: counts.total || 0,
        percent: counts.total > 0 ? Math.round((counts.completed / counts.total) * 100) : (normalizeState(safeRoot) === 'completed' ? 100 : 0),
    };
    if (typeof progress.percent !== 'number') {
        progress.percent = progress.total > 0 ? Math.round((progress.completed / progress.total) * 100) : 0;
    }

    const verification = data.verification && typeof data.verification === 'object' ? { ...data.verification } : {};
    if (!verification.verdict) {
        const verifyTask = [...childTasks].reverse().find(task => {
            const title = String(task?.title || task?.name || '').toLowerCase();
            const metadata = task && typeof task.metadata === 'object' ? task.metadata : {};
            return metadata.agent_type === 'verify' || title.includes('verify') || title.includes('验证');
        });
        if (verifyTask) {
            const verdictText = `${verifyTask.result_summary || ''}\n${verifyTask.blocked_reason || ''}`.toUpperCase();
            if (verdictText.includes('VERDICT: PASS') || verifyTask.state === 'completed') verification.verdict = 'PASS';
            else if (verdictText.includes('VERDICT: PARTIAL')) verification.verdict = 'PARTIAL';
            else if (verdictText.includes('VERDICT: FAIL') || ['failed', 'blocked'].includes(normalizeState(verifyTask))) verification.verdict = 'FAIL';
            verification.task_id = verifyTask.id || verification.task_id || '';
            verification.state = normalizeState(verifyTask) || verification.state || '';
            verification.summary = verification.summary || verifyTask.result_summary || verifyTask.blocked_reason || '';
        }
    }

    return {
        type: 'task_update',
        schema_version: 1,
        root_task: safeRoot,
        child_tasks: childTasks,
        tasks: childTasks,
        task: safeRoot,
        counts,
        progress,
        verification,
        session_id: data.session_id || '',
        source: data.source || '',
    };
}

function _findTaskStatusBarBody(host) {
    // Body may have been portaled to document.body by drag handler
    return (host && host.querySelector('.task-status-bar__body.task-status-bar__body--expanded'))
        || document.querySelector('#taskStatusBar > .task-status-bar__body.task-status-bar__body--expanded')
        || document.querySelector('body > .task-status-bar__body.task-status-bar__body--expanded');
}

function positionTaskStatusBarBody(host) {
    if (!host) return;
    const body = _findTaskStatusBarBody(host);
    if (!body) return;
    body.style.transition = 'all 0.2s ease';
    body.style.position = 'fixed';
    body.style.bottom = 'auto';
    body.style.transform = 'none';
    body.style.zIndex = taskStatusBarAlwaysOnTop ? '2147483002' : '2147483001';
    host.style.zIndex = taskStatusBarAlwaysOnTop ? '2147483001' : '2147483000';
    body.dataset.taskStatusBarPopupMode = taskStatusBarPopupMode;
    switch (taskStatusBarPopupMode) {
        case 'maximized':
            body.style.left = '20px';
            body.style.top = '20px';
            body.style.right = '20px';
            body.style.width = 'calc(100vw - 40px)';
            body.style.maxWidth = 'calc(100vw - 40px)';
            body.style.maxHeight = 'calc(100vh - 40px)';
            body.style.height = '';
            body.style.borderRadius = '14px';
            break;
        case 'fullscreen':
            body.style.left = '0';
            body.style.top = '0';
            body.style.right = '0';
            body.style.width = '100vw';
            body.style.maxWidth = '100vw';
            body.style.maxHeight = '100vh';
            body.style.height = '';
            body.style.borderRadius = '0';
            break;
        case 'centered': {
            const w = Math.min(600, Math.max(320, window.innerWidth - 48));
            const h = Math.min(520, Math.max(260, window.innerHeight - 48));
            body.style.width = `${w}px`;
            body.style.maxWidth = `${w}px`;
            body.style.maxHeight = `${h}px`;
            body.style.height = '';
            body.style.left = `${Math.max(24, Math.round((window.innerWidth - w) / 2))}px`;
            body.style.top = `${Math.max(24, Math.round((window.innerHeight - h) / 2))}px`;
            body.style.right = 'auto';
            body.style.borderRadius = '14px';
            break;
        }
        case 'left':
            body.style.left = '10px';
            body.style.top = '10px';
            body.style.right = 'auto';
            body.style.width = 'calc(50vw - 20px)';
            body.style.maxWidth = 'calc(50vw - 20px)';
            body.style.maxHeight = 'calc(100vh - 20px)';
            body.style.height = '';
            body.style.borderRadius = '14px';
            break;
        case 'right':
            body.style.left = 'auto';
            body.style.top = '10px';
            body.style.right = '10px';
            body.style.width = 'calc(50vw - 20px)';
            body.style.maxWidth = 'calc(50vw - 20px)';
            body.style.maxHeight = 'calc(100vh - 20px)';
            body.style.height = '';
            body.style.borderRadius = '14px';
            break;
        case 'custom': {
            const popupLeft = taskStatusBarCustomLeft || body.dataset.taskStatusBarPopupLeft || body.style.left;
            const popupTop = taskStatusBarCustomTop || body.dataset.taskStatusBarPopupTop || body.style.top;
            const popupWidth = taskStatusBarCustomWidth || body.dataset.taskStatusBarPopupWidth || body.style.width;
            const popupHeight = taskStatusBarCustomHeight || body.dataset.taskStatusBarPopupHeight || body.style.height;
            if (!popupLeft || !popupTop) {
                // No saved custom position — fall back to default positioning
                console.log('[taskStatusBar] custom mode but no saved position, falling back to default');
                taskStatusBarPopupMode = 'default';
                const hostRect = host.getBoundingClientRect();
                body.style.right = 'auto';
                body.style.bottom = 'auto';
                body.style.transform = 'none';
                body.style.width = '';
                body.style.maxWidth = '';
                body.style.maxHeight = '';
                body.style.height = '';
                body.style.borderRadius = '14px';
                body.style.left = `${Math.max(8, Math.round(hostRect.right - 520))}px`;
                body.style.top = `${Math.max(8, Math.round(hostRect.top - 420))}px`;
                break;
            }
            body.style.left = popupLeft;
            body.style.top = popupTop;
            body.style.right = 'auto';
            if (popupWidth) body.style.width = popupWidth;
            if (popupHeight) body.style.height = popupHeight;
            body.style.maxWidth = 'none';
            body.style.maxHeight = 'calc(100vh - 20px)';
            body.style.borderRadius = '14px';
            break;
        }
        default: {
            const hostRect = host.getBoundingClientRect();
            body.style.right = 'auto';
            body.style.bottom = 'auto';
            body.style.transform = 'none';
            body.style.width = '';
            body.style.maxWidth = '';
            body.style.maxHeight = '';
            body.style.height = '';
            body.style.borderRadius = '14px';
            body.style.left = `${Math.max(8, Math.round(hostRect.right - 520))}px`;
            body.style.top = `${Math.max(8, Math.round(hostRect.top - 420))}px`;
            break;
        }
    }
}

function getTaskStatusBarPopupPayload(sessionId = currentSession || currentChatId) {
    return taskStatusBarCache || loadTaskStatusBarCache(sessionId) || { root_task: {}, child_tasks: [] };
}

function refreshTaskStatusBarPopup(mode = 'default', expanded = true) {
    taskStatusBarPopupMode = mode;
    taskStatusBarBodyExpanded = expanded;
    const payload = getTaskStatusBarPopupPayload();
    const host = document.getElementById('taskStatusBar');
    if (host) {
        host.style.zIndex = expanded ? (taskStatusBarAlwaysOnTop ? '2147483001' : '2147483000') : '18';
    }
    renderTaskStatusBar(payload, { emptyMessage: '当前会话任务状态已更新。' });
    positionTaskStatusBarBody(host);
    return host;
}

function getTaskStatusBarPopupBody(panel) {
    if (!panel || panel.id !== 'taskStatusBar') return null;
    return _findTaskStatusBarBody(panel);
}

function updateTaskStatusBarPopupPositionFromRect(body, rect) {
    if (!body || !rect) return;
    body.dataset.taskStatusBarPopupLeft = `${Math.max(8, Math.round(rect.left))}px`;
    body.dataset.taskStatusBarPopupTop = `${Math.max(8, Math.round(rect.top))}px`;
    body.dataset.taskStatusBarPopupWidth = `${Math.max(320, Math.round(rect.width))}px`;
    body.dataset.taskStatusBarPopupHeight = `${Math.max(220, Math.round(rect.height))}px`;
}

function renderTaskStatusBar(data = {}, options = {}) {
    const normalized = normalizeTaskUpdatePayload(data);
    const cachedTaskId = loadCachedCurrentTaskId();
    let rootTask = normalized.root_task || {};
    let childTasks = normalized.child_tasks || [];
    const hasLiveRoot = Boolean(rootTask && rootTask.id);
    if (!hasLiveRoot && cachedTaskId) {
        rootTask = {
            id: cachedTaskId,
            title: options.loadingTitle || '当前任务加载中…',
            objective: options.loadingObjective || '等待首个 task_update 事件到达',
            current_step: options.loadingObjective || '等待首个 task_update 事件到达',
            state: 'loading',
        };
        childTasks = [];
    }
    const state = String(rootTask.state || rootTask.status || '').trim().toLowerCase();
    const host = ensureTaskStatusBar();
    const title = rootTask.title || rootTask.objective || '当前会话任务';
    const currentStep = rootTask.current_step || rootTask.objective || '等待任务启动';
    const progressPercent = Math.max(0, Math.min(100, Number(normalized.progress?.percent ?? 0)));
    const completedSteps = Number(normalized.progress?.completed ?? normalized.counts?.completed ?? 0);
    const totalSteps = Number(normalized.progress?.total ?? normalized.counts?.total ?? 0);
    const statusLabels = {
        created: '待创建',
        planned: '已规划',
        in_progress: '进行中',
        waiting_approval: '等待审批',
        verifying: '验证中',
        completed: '已完成',
        failed: '失败',
        blocked: '已阻塞',
        cancelled: '已取消',
    };
    const statusClasses = {
        created: 'task-status-bar__status-pill--created',
        planned: 'task-status-bar__status-pill--planned',
        in_progress: 'task-status-bar__status-pill--in_progress',
        waiting_approval: 'task-status-bar__status-pill--waiting_approval',
        verifying: 'task-status-bar__status-pill--verifying',
        completed: 'task-status-bar__status-pill--completed',
        failed: 'task-status-bar__status-pill--failed',
        blocked: 'task-status-bar__status-pill--blocked',
        cancelled: 'task-status-bar__status-pill--cancelled',
    };
    const statusLabel = statusLabels[state] || (state ? state : '未知状态');
    const statusClass = statusClasses[state] || 'task-status-bar__status-pill--created';
    const verificationVerdict = String(normalized.verification?.verdict || '').trim().toUpperCase();
    const verificationLabel = verificationVerdict || (state === 'verifying' ? 'PENDING' : '未验证');
    const verificationClass = `task-status-bar__verify-pill--${verificationLabel === '未验证' ? 'PENDING' : verificationLabel}`;
    const verificationSummary = normalized.verification?.summary || (state === 'verifying' ? '正在等待 verify 结论' : '暂无验证结论');
    const isInterventionState = ['waiting_approval', 'blocked', 'failed'].includes(state);
    const alertMessage = state === 'waiting_approval'
        ? (rootTask.blocked_reason || '任务进入等待审批状态，请你介入确认后继续推进。')
        : (['blocked', 'failed'].includes(state)
            ? (rootTask.blocked_reason || '任务已阻塞，请查看失败原因并介入处理。')
            : '');

    host.className = `task-status-bar ${statusClass} ${isInterventionState ? 'task-status-bar--warning' : ''} ${['blocked', 'failed'].includes(state) ? 'task-status-bar--blocked' : ''}`.trim();
    host.dataset.state = state || '';
    host.title = taskStatusBarBodyExpanded ? '点击折叠详情' : '点击展开详情';
    const effectiveTaskId = String(rootTask.id || cachedTaskId || '').trim();
    if (effectiveTaskId && taskStatusBarTreeTaskId !== effectiveTaskId) {
        taskStatusBarTreeTaskId = effectiveTaskId;
        taskStatusBarTreeExpanded = false;
    }
    if (effectiveTaskId) {
        cacheCurrentTaskId(effectiveTaskId, 'renderTaskStatusBar');
    }
    host.classList.remove('task-status-bar--empty');
    host.style.zIndex = taskStatusBarAlwaysOnTop ? '2147483001' : '2147483000';
    host.dataset.dragSuppressClick = host.dataset.dragSuppressClick === 'true' ? 'true' : 'false';
    host.onclick = (event) => {
        if (host.dataset.dragSuppressClick === 'true') {
            event.preventDefault();
            event.stopPropagation();
            host.dataset.dragSuppressClick = 'false';
            return;
        }
        const control = event?.target?.closest?.('.task-status-bar__tree-toggle, .task-status-bar__task-link, .task-status-bar__copy-task-id, .task-status-bar__copy-root-task');
        if (control) return;
        activateShortcutPanel(host);
    };

    if (taskStatusBarEscHandler) {
        document.removeEventListener('keydown', taskStatusBarEscHandler);
    }
    taskStatusBarEscHandler = (event) => {
        if (event.key === 'Escape' && taskStatusBarBodyExpanded) {
            event.preventDefault();
            taskStatusBarBodyExpanded = false;
            taskStatusBarPopupMode = 'default';
            taskStatusBarCustomLeft = '';
            taskStatusBarCustomTop = '';
            taskStatusBarCustomWidth = '';
            taskStatusBarCustomHeight = '';
            const payload = taskStatusBarCache || loadTaskStatusBarCache(currentSession || currentChatId) || { root_task: {}, child_tasks: [] };
            renderTaskStatusBar(payload, { emptyMessage: '当前会话任务已更新。' });
        }
    };
    document.addEventListener('keydown', taskStatusBarEscHandler);

    if (isInterventionState) {
        const alertKey = `${rootTask.id || 'root'}:${state}:${alertMessage}`;
        if (taskStatusBarLastAlert !== alertKey) {
            taskStatusBarLastAlert = alertKey;
            showNotification(
                state === 'waiting_approval'
                    ? '任务进入等待审批，请介入处理。'
                    : '任务已阻塞，请查看状态栏中的原因。',
                state === 'waiting_approval' ? 'warn' : 'error'
            );
        }
    }

    const verifyText = verificationSummary ? escapeHtml(verificationSummary) : '暂无验证结论';
    const childCountText = childTasks.length > 0 ? `${childTasks.length} 个子任务` : '暂无子任务';
    const stepCountText = totalSteps > 0 ? `${completedSteps}/${totalSteps} 步` : `${completedSteps} 步`;
    const warningHtml = alertMessage ? `<div class="task-status-bar__alert">⚠️ ${escapeHtml(alertMessage)}</div>` : '';
    const copyButtonHtml = effectiveTaskId ? `
        <button type="button" class="task-status-bar__copy-task-id" data-task-id="${escapeHtml(effectiveTaskId)}" title="复制当前根任务ID">
            <span>📋</span>
            <span>复制</span>
        </button>
    ` : '';

    if (!rootTask || !rootTask.id) {
        host.classList.add('task-status-bar--empty');
        host.innerHTML = `
            <div class="task-status-bar__empty-state">
                <i class="fas fa-layer-group"></i>
                <span>${escapeHtml(options.emptyMessage || '当前会话还没有生成任务树，等待任务开始。')}</span>
            </div>
        `;
        return normalized;
    }

    const canToggleTree = childTasks.length > 0;
    const treeChildrenHtml = canToggleTree && taskStatusBarTreeExpanded
        ? `<div class="task-status-bar__tree-children">${childTasks.map(task => buildTaskTreeNodeHtml(task)).join('')}</div>`
        : '';
    const rootSummary = rootTask.objective || rootTask.current_step || options.loadingObjective || '等待任务更新';
    const treeButtonLabel = taskStatusBarTreeExpanded ? '收起子任务' : `展开子任务 (${childTasks.length})`;
    const cachedIdHtml = cachedTaskId && !hasLiveRoot ? `<span class="task-status-bar__tree-root-id"><i class="fas fa-hashtag"></i>${escapeHtml(cachedTaskId)}</span>` : '';
    const rootNodeHtml = `
        <div class="task-status-bar__tree-node task-status-bar__tree-node--root task-status-bar__tree-root ${state ? `task-state--${escapeHtml(state)}` : ''}" data-task-id="${escapeHtml(rootTask.id || '')}" role="button" tabindex="0" aria-expanded="${taskStatusBarTreeExpanded ? 'true' : 'false'}">
            <span class="task-status-bar__tree-root-actions">
                <button type="button" class="task-status-bar__copy-root-task" data-task-id="${escapeHtml(rootTask.id || cachedTaskId || '')}" aria-label="复制任务ID" title="复制">
                    <span aria-hidden="true">📋</span>
                </button>
            </span>
            <span class="task-status-bar__tree-toggle-icon"><i class="fas ${taskStatusBarTreeExpanded ? 'fa-chevron-down' : 'fa-chevron-right'}"></i></span>
            <div class="task-status-bar__tree-root-main">
                <div class="task-status-bar__tree-root-title">
                    <span class="task-status-bar__tree-root-title-text">${escapeHtml(title)}</span>
                    <span class="task-status-bar__tree-state-badge ${state ? `task-state--${escapeHtml(state)}` : ''}">${escapeHtml(statusLabel)}</span>
                </div>
                <div class="task-status-bar__tree-root-summary">${escapeHtml(rootSummary)}</div>
                <div class="task-status-bar__tree-root-meta">
                    <span class="task-status-bar__tree-root-id"><i class="fas fa-folder-tree"></i><span>${escapeHtml(childCountText)}</span></span>
                    <span class="task-status-bar__tree-root-id"><i class="fas fa-list-ol"></i><span>${escapeHtml(stepCountText)}</span></span>
                    ${cachedIdHtml}
                </div>
            </div>
            <span class="task-status-bar__tree-toggle ${canToggleTree ? '' : 'task-status-bar__tree-toggle--disabled'}" data-task-tree-toggle="true">
                <i class="fas ${taskStatusBarTreeExpanded ? 'fa-chevron-up' : 'fa-chevron-down'}"></i>
                <span>${escapeHtml(treeButtonLabel)}</span>
            </span>
        </div>
    `;

    // Clean up any previously portaled body from document.body
    const oldPortaledBody = document.querySelector('body > .task-status-bar__body.task-status-bar__body--expanded');
    if (oldPortaledBody) oldPortaledBody.remove();

    host.innerHTML = `
        <div class="task-status-bar__header">
            <div class="task-status-bar__title-wrap">
                <div class="task-status-bar__eyebrow">任务状态栏</div>
                <div class="task-status-bar__title">${escapeHtml(title)}<span class="task-status-bar__collapse-hint">${taskStatusBarBodyExpanded ? '(点击折叠 / ESC)' : '(点击展开)'}</span></div>
                <div class="task-status-bar__subtitle">${escapeHtml(currentStep)}</div>
            </div>
            <div class="task-status-bar__status-pill ${statusClass}">
                <i class="fas fa-circle"></i>
                <span>${escapeHtml(statusLabel)}</span>
            </div>
        </div>
        <div class="task-status-bar__body ${taskStatusBarBodyExpanded && !taskStatusBarBodyMinimized ? 'task-status-bar__body--expanded' : 'task-status-bar__body--collapsed'} ${taskStatusBarBodyMinimized ? 'task-status-bar__body--minimized' : ''}">
            <div class="task-status-bar__meta">
                <span><strong>任务ID</strong> <button type="button" class="task-status-bar__task-link" data-task-id="${escapeHtml(rootTask.id || cachedTaskId || '')}" title="点击查看任务状态历史"><i class="fas fa-hashtag"></i><span>${escapeHtml(rootTask.id || cachedTaskId || '加载中…')}</span></button></span>
                <span><strong>进度</strong> ${escapeHtml(stepCountText)}</span>
                <span><strong>子任务</strong> ${escapeHtml(childCountText)}</span>
                <button type="button" class="task-status-bar__minimize-btn" data-task-status-bar-minimize="true" title="最小化"><i class="fas fa-minus"></i></button>
                <button type="button" class="task-status-bar__close-btn" data-task-status-bar-close="true" title="关闭弹窗"><i class="fas fa-times"></i></button>
            </div>
            <div class="task-status-bar__progress-line">
                <span>步骤进度</span>
                <span>${progressPercent}%</span>
            </div>
            <div class="task-status-bar__progress-track" aria-label="任务步骤进度">
                <div class="task-status-bar__progress-fill" style="width: ${progressPercent}%;"></div>
            </div>
            <div class="task-status-bar__verify-row">
                <span>Verify 结论</span>
                <span class="task-status-bar__verify-pill ${verificationClass}">
                    <i class="fas fa-shield-alt"></i>
                    <span>${escapeHtml(verificationLabel)}</span>
                </span>
            </div>
            <div class="task-status-bar__subtitle">${verifyText}</div>
            ${warningHtml}
            <div class="task-status-bar__tree-panel">
                <div class="task-status-bar__tree-panel-actions">
                    ${copyButtonHtml}
                </div>
                <div class="task-status-bar__tree">
                    ${rootNodeHtml}
                    ${treeChildrenHtml}
                    ${!canToggleTree && taskStatusBarTreeExpanded ? '<div class="task-status-bar__empty-state"><i class="fas fa-info-circle"></i><span>该任务暂无子任务。</span></div>' : ''}
                </div>
            </div>
            <div class="task-status-bar__resize-handle" title="拖动调整大小"></div>
        </div>
    `;

    const header = host.querySelector('.task-status-bar__header');
    if (header) {
        bindTaskStatusBarHeaderDrag(host, header);
        header.onclick = (event) => {
            // Toggle is handled in pointerup (bindTaskStatusBarHeaderDrag)
            // for robustness against SSE-driven DOM replacement.
            // This handler just prevents default + stops propagation.
            if (host.dataset.dragSuppressClick === 'true') {
                host.dataset.dragSuppressClick = 'false';
                return;
            }
            const control = event?.target?.closest?.('.task-status-bar__status-pill');
            if (control) return;
            event.preventDefault();
            event.stopPropagation();
        };
        header.oncontextmenu = (event) => {
            event.preventDefault();
            event.stopPropagation();
            activateShortcutPanel(host);
            showPanelContextMenu(event.clientX, event.clientY, host);
        };
    }

    // Portal the expanded body to document.body FIRST so position:fixed is
    // relative to the viewport (host has backdrop-filter which creates
    // a containing block for fixed descendants). Must portal BEFORE
    // positionTaskStatusBarBody so all coordinates are viewport-relative.
    const bodyPanel = host.querySelector('.task-status-bar__body.task-status-bar__body--expanded');
    if (bodyPanel) {
        document.body.appendChild(bodyPanel);
    }

    positionTaskStatusBarBody(host);

    if (bodyPanel) {
        bindTaskStatusBarBodyDrag(host, bodyPanel);
        bodyPanel.oncontextmenu = (event) => {
            if (event.target?.closest?.('button, a, input, textarea, select')) return;
            event.preventDefault();
            event.stopPropagation();
            activateShortcutPanel(host);
            showPanelContextMenu(event.clientX, event.clientY, host);
        };
    }

    const storageKey = getTaskStatusBarStorageKey(normalized.session_id || currentSession || currentChatId);
    taskStatusBarCache = normalized;
    if (effectiveTaskId) {
        cacheCurrentTaskId(effectiveTaskId, 'renderTaskStatusBar');
    }
    try {
        localStorage.setItem(storageKey, JSON.stringify(normalized));
    } catch (e) {
        console.warn('[renderTaskStatusBar] Failed to persist task status bar cache:', e);
    }

    const taskLink = (bodyPanel || host).querySelector('.task-status-bar__task-link');
    if (taskLink) {
        taskLink.onclick = (event) => {
            event.preventDefault();
            const taskId = taskLink.dataset.taskId || rootTask.id || '';
            onTaskIdClicked(taskId);
        };
    }

    const titleTrigger = host.querySelector('.task-status-bar__title');
    if (titleTrigger) {
        titleTrigger.style.cursor = 'pointer';
        titleTrigger.title = taskStatusBarBodyExpanded ? '点击折叠' : '点击展开';
    }

    const closeBtn = (bodyPanel || host).querySelector('.task-status-bar__close-btn');
    if (closeBtn) {
        closeBtn.onclick = (event) => {
            event.preventDefault();
            event.stopPropagation();
            taskStatusBarBodyExpanded = false;
            taskStatusBarBodyMinimized = false;
            taskStatusBarPopupMode = 'default';
            taskStatusBarCustomLeft = '';
            taskStatusBarCustomTop = '';
            taskStatusBarCustomWidth = '';
            taskStatusBarCustomHeight = '';
            renderTaskStatusBar({ ...normalized, root_task: rootTask, child_tasks: childTasks }, options);
        };
    }

    const minimizeBtn = (bodyPanel || host).querySelector('.task-status-bar__minimize-btn');
    if (minimizeBtn) {
        minimizeBtn.onclick = (event) => {
            event.preventDefault();
            event.stopPropagation();
            if (taskStatusBarBodyMinimized) {
                taskStatusBarBodyMinimized = false;
                taskStatusBarBodyExpanded = true;
            } else {
                taskStatusBarBodyMinimized = true;
            }
            renderTaskStatusBar({ ...normalized, root_task: rootTask, child_tasks: childTasks }, options);
        };
    }

    const copyRootButton = (bodyPanel || host).querySelector('.task-status-bar__copy-root-task');
    if (copyRootButton) {
        copyRootButton.onclick = async (event) => {
            event.preventDefault();
            event.stopPropagation();
            const taskId = String(copyRootButton.dataset.taskId || rootTask.id || cachedTaskId || '').trim();
            if (!taskId) return;
            try {
                await navigator.clipboard.writeText(taskId);
                copyRootButton.classList.add('is-copied');
                showNotification(`已复制任务ID: ${taskId}`, 'success');
                window.setTimeout(() => copyRootButton.classList.remove('is-copied'), 1200);
            } catch (error) {
                showNotification('复制任务ID失败', 'error');
            }
        };
    }

    const copyTaskIdButton = (bodyPanel || host).querySelector('.task-status-bar__copy-task-id');
    if (copyTaskIdButton) {
        copyTaskIdButton.onclick = async (event) => {
            event.preventDefault();
            event.stopPropagation();
            const taskId = String(copyTaskIdButton.dataset.taskId || rootTask.id || cachedTaskId || '').trim();
            if (!taskId) {
                showNotification('当前没有可复制的任务ID', 'warning');
                return;
            }
            const copied = await copyTextToClipboard(taskId);
            if (copied) {
                showNotification(`已复制任务ID: ${taskId}`, 'success');
            } else {
                showNotification('复制任务ID失败', 'error');
            }
        };
    }

    const treeRoot = (bodyPanel || host).querySelector('.task-status-bar__tree-root');
    if (treeRoot) {
        treeRoot.onclick = (event) => {
            event.preventDefault();
            event.stopPropagation();
            if (!canToggleTree) return;
            taskStatusBarTreeExpanded = !taskStatusBarTreeExpanded;
            renderTaskStatusBar({ ...normalized, root_task: rootTask, child_tasks: childTasks }, options);
        };
    }

    return normalized;
}

async function refreshTaskStatusBar(sessionId = currentSession || currentChatId, options = {}) {
    ensureTaskStatusBar();

    const normalizedFallback = taskStatusBarCache || loadTaskStatusBarCache(sessionId) || { root_task: {}, child_tasks: [] };
    if (!sessionId) {
        return renderTaskStatusBar(normalizedFallback, { emptyMessage: options.emptyMessage || '当前还没有激活会话任务。' });
    }

    if (!options.force) {
        renderTaskStatusBar(normalizedFallback, { emptyMessage: options.emptyMessage || '正在加载当前任务状态…' });
    }

    try {
        const response = await fetch(`${CONFIG.API_URL}/api/sessions/${encodeURIComponent(sessionId)}`, {
            method: 'GET',
            headers: { 'Content-Type': 'application/json' }
        }).catch(() => null);

        if (response && response.ok) {
            cacheCurrentTaskIdFromResponse(response, 'refreshTaskStatusBar');
            const data = await response.json();
            const payload = data.task_update || normalizedFallback;
            return renderTaskStatusBar({ ...payload, session_id: sessionId }, { emptyMessage: options.emptyMessage });
        }
    } catch (error) {
        console.error('[refreshTaskStatusBar] 加载任务状态失败:', error);
    }

    return renderTaskStatusBar(normalizedFallback, { emptyMessage: options.emptyMessage || '当前任务状态暂不可用。' });
}

function loadTaskStatusBarCache(sessionId = currentSession || currentChatId) {
    try {
        const raw = localStorage.getItem(getTaskStatusBarStorageKey(sessionId));
        if (!raw) return null;
        return normalizeTaskUpdatePayload(JSON.parse(raw));
    } catch (e) {
        console.warn('[loadTaskStatusBarCache] Failed to parse cached task status:', e);
        return null;
    }
}

function applyTaskUpdateEvent(data, sessionId = currentSession || currentChatId) {
    const normalized = normalizeTaskUpdatePayload({ ...data, session_id: sessionId });
    const rootTaskId = normalized?.root_task?.id || '';
    if (rootTaskId) {
        cacheCurrentTaskId(rootTaskId, 'task_update');
    }
    renderTaskStatusBar(normalized, { emptyMessage: '当前会话任务已更新。' });
    // P1-2: Update sidebar task progress panel
    updateSessionTask(normalized);
    return normalized;
}

// 渲染任务面板
function renderTaskPanel(data) {
    const sectionsDiv = document.getElementById('taskSections');
    const statusDiv = document.getElementById('taskSchedulerStatus');
    
    if (!sectionsDiv) return;
    
    // 更新统计数据
    const tasks = data.tasks || [];
    const pending = tasks.filter(t => t.status === 'pending').length;
    const running = tasks.filter(t => t.status === 'running').length;
    // 已完成：状态为completed 或 run_count > 0（周期任务执行后回到pending）
    const completed = tasks.filter(t => t.status === 'completed' || (t.run_count && t.run_count > 0)).length;
    const failed = tasks.filter(t => t.status === 'failed').length;
    
    document.getElementById('statTotal').textContent = tasks.length;
    document.getElementById('statPending').textContent = pending;
    document.getElementById('statRunning').textContent = running;
    document.getElementById('statCompleted').textContent = completed;
    
    // 更新调度器状态
    if (statusDiv) {
        if (data.scheduler_running) {
            statusDiv.className = 'task-scheduler-running';
            statusDiv.innerHTML = '<i class="fas fa-circle"></i> 调度器运行中';
        } else {
            statusDiv.className = 'task-scheduler-stopped';
            statusDiv.innerHTML = `
                <div style="display: flex; align-items: center; gap: 15px;">
                    <span><i class="fas fa-pause-circle"></i> 调度器已停止</span>
                    <button class="btn-start-scheduler" onclick="startTaskScheduler()" title="启动任务调度器">
                        <i class="fas fa-play"></i> 启动调度器
                    </button>
                </div>
            `;
        }
    }
    
    // 分离任务
    const pendingTasks = tasks.filter(t => t.status === 'pending');
    const runningTasks = tasks.filter(t => t.status === 'running');
    const completedTasks = tasks.filter(t => t.status === 'completed').slice(0, 5); // 只显示最近5个
    const failedTasks = tasks.filter(t => t.status === 'failed');
    
    let html = '';
    
    // 执行中任务
    if (runningTasks.length > 0) {
        html += `
            <div class="task-section">
                <h4 class="task-section-title" style="color:#3b82f6;"><i class="fas fa-spinner fa-spin"></i> 执行中 (${runningTasks.length})</h4>
                <div class="task-list">
                    ${runningTasks.map(task => renderTaskItem(task)).join('')}
                </div>
            </div>
        `;
    }
    
    // 待执行任务
    if (pendingTasks.length > 0) {
        html += `
            <div class="task-section">
                <h4 class="task-section-title"><i class="fas fa-clock"></i> 待执行任务 (${pendingTasks.length})</h4>
                <div class="task-list">
                    ${pendingTasks.map(task => renderTaskItem(task)).join('')}
                </div>
            </div>
        `;
    }
    
    // 失败任务
    if (failedTasks.length > 0) {
        html += `
            <div class="task-section">
                <h4 class="task-section-title"><i class="fas fa-exclamation-triangle"></i> 失败任务 (${failedTasks.length})</h4>
                <div class="task-list">
                    ${failedTasks.map(task => renderTaskItem(task)).join('')}
                </div>
            </div>
        `;
    }
    
    // 已完成任务
    if (completedTasks.length > 0) {
        html += `
            <div class="task-section">
                <h4 class="task-section-title"><i class="fas fa-check-circle"></i> 最近已完成</h4>
                <div class="task-list">
                    ${completedTasks.map(task => renderTaskItem(task)).join('')}
                </div>
            </div>
        `;
    }
    
    // 空状态
    if (html === '') {
        html = `
            <div class="task-empty">
                <i class="fas fa-clipboard-check"></i>
                <div>暂无任务数据</div>
            </div>
        `;
    }
    
    sectionsDiv.innerHTML = html;
}

// 渲染单个任务项
function renderTaskItem(task) {
    // 调试：检查任务数据
    console.log('[renderTaskItem] task:', task.id, 'status:', task.status, 'typeof status:', typeof task.status);
    
    const statusLabels = {
        'pending': '待执行',
        'completed': '已完成',
        'failed': '失败',
        'running': '运行中'
    };
    
    const nextRun = task.next_run ? formatTaskTime(task.next_run) : '未安排';
    const lastRun = task.last_run ? formatTaskTime(task.last_run) : '从未执行';
    const displayName = task.name || '未命名任务';
    const description = task.description || '执行预定维护任务';
    
    // 调试：强制显示按钮来测试
    const shouldShowButton = (task.status === 'pending' || task.status === 'failed' || task.status === '失败');
    console.log('[DEBUG] task.id:', task.id, 'status:', task.status, 'shouldShow:', shouldShowButton);
    
    const executeNowButton = shouldShowButton ? `
        <button class="btn-execute-now" onclick="event.stopPropagation(); executeTaskNow('${task.id}')" 
            style="background: linear-gradient(135deg, #10b981 0%, #059669 100%); color: white; border: none; 
                   padding: 2px 10px; border-radius: 4px; font-size: 11px; cursor: pointer; 
                   display: inline-flex; align-items: center; gap: 4px; transition: all 0.2s;
                   line-height: 1.2;">
            <i class="fas fa-bolt"></i> 立刻执行
        </button>
    ` : '';
    
    return `
        <div class="task-item ${task.status}" onclick="handleTaskClick('${task.id}')">
            <div class="task-item-header">
                <span class="task-item-name">${displayName}</span>
                <div style="display: flex; align-items: center; gap: 8px;">
                    ${executeNowButton}
                    <span class="task-item-status ${task.status}">${statusLabels[task.status] || task.status}</span>
                </div>
            </div>
            <div class="task-item-description" style="font-size:12px;color:#94a3b8;margin:8px 0;line-height:1.4;">
                ${description}
            </div>
            <div class="task-item-meta">
                <span><i class="fas fa-play-circle"></i> ${task.schedule_type === 'daily' ? '每日' : '每' + Math.floor(task.interval_seconds / 60) + '分钟'}</span>
                ${task.status === 'pending' 
                    ? `<span><i class="fas fa-hourglass-half"></i> 下次: ${nextRun}</span>`
                    : `<span><i class="fas fa-history"></i> 上次: ${lastRun}</span>`
                }
            </div>
        </div>
    `;
}

// 格式化任务时间
function formatTaskTime(isoString) {
    const date = new Date(isoString);
    const now = new Date();
    const diff = date - now;
    
    // 如果是今天
    if (date.toDateString() === now.toDateString()) {
        return `今天 ${date.getHours().toString().padStart(2, '0')}:${date.getMinutes().toString().padStart(2, '0')}`;
    }
    
    // 如果是明天
    const tomorrow = new Date(now);
    tomorrow.setDate(tomorrow.getDate() + 1);
    if (date.toDateString() === tomorrow.toDateString()) {
        return `明天 ${date.getHours().toString().padStart(2, '0')}:${date.getMinutes().toString().padStart(2, '0')}`;
    }
    
    // 其他情况
    return `${date.getMonth() + 1}/${date.getDate()} ${date.getHours().toString().padStart(2, '0')}:${date.getMinutes().toString().padStart(2, '0')}`;
}

// 处理任务点击
function handleTaskClick(taskId) {
    // 关闭面板
    closeTaskSchedulerPanel();
    
    // 在聊天界面创建一条关于此任务的消息
    const task = taskDataCache?.tasks?.find(t => t.id === taskId);
    if (!task) return;
    
    const message = `请帮我查看和优化这个任务的执行情况：

**任务名称**: ${task.name}
**执行命令**: \`${task.command}\`
**调度类型**: ${task.schedule_type === 'daily' ? '每日执行' : '间隔执行（每' + Math.floor(task.interval_seconds / 60) + '分钟）'}
**当前状态**: ${task.status === 'pending' ? '待执行' : task.status === 'completed' ? '已完成' : '失败'}
**下次执行**: ${task.next_run ? new Date(task.next_run).toLocaleString('zh-CN') : '未安排'}

请检查该任务是否能正常运行，如果有问题请帮我修复。`;
    
    // 添加到输入框并聚焦
    const input = document.getElementById('userInput');
    if (input) {
        input.value = message;
        input.focus();
        // 调整高度
        input.style.height = 'auto';
        input.style.height = input.scrollHeight + 'px';
    }
}

// 更新任务徽章
function updateTaskBadge(data) {
    const badge = document.getElementById('taskBadge');
    const btn = document.getElementById('taskSchedulerBtn');
    
    if (!badge || !btn) return;
    
    const pending = (data.tasks || []).filter(t => t.status === 'pending').length;
    
    if (pending > 0) {
        badge.textContent = pending;
        badge.style.display = 'inline';
        btn.classList.add('has-tasks');
    } else {
        badge.style.display = 'none';
        btn.classList.remove('has-tasks');
    }
}

// 渲染任务错误
function renderTaskError(message) {
    const sectionsDiv = document.getElementById('taskSections');
    if (sectionsDiv) {
        sectionsDiv.innerHTML = `
            <div class="task-empty">
                <i class="fas fa-exclamation-circle" style="color: #ef4444;"></i>
                <div>${message}</div>
            </div>
        `;
    }
}

// 启动任务调度器
async function startTaskScheduler() {
    try {
        const response = await fetch(`${CONFIG.API_URL}/api/tasks/scheduler/start`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        
        if (response.ok) {
            const result = await response.json();
            if (result.success) {
                showNotification('调度器已启动', 'success');
                // 刷新任务状态
                refreshTaskStatus();
            } else {
                showNotification('启动失败: ' + result.error, 'error');
            }
        } else {
            showNotification('启动调度器失败', 'error');
        }
    } catch (error) {
        console.error('[startTaskScheduler] 启动失败:', error);
        showNotification('启动调度器失败: ' + error.message, 'error');
    }
}

// 立刻执行指定任务
async function executeTaskNow(taskId) {
    try {
        // 先添加用户请求到聊天历史
        const taskItem = document.querySelector(`[onclick="handleTaskClick('${taskId}')"]`);
        const taskName = taskItem?.querySelector('.task-item-name')?.textContent || taskId;
        
        // 构建用户消息内容
        const userMessage = `🚀 **立即执行任务**: ${taskName} (ID: ${taskId})`;
        
        // 添加到聊天界面和历史
        addMessageToUI('user', userMessage, false, false, []);
        messageHistory.push({ role: 'user', content: userMessage });
        saveChatToHistory();
        
        // 显示正在执行提示
        showNotification('正在执行任务...', 'info');
        
        const response = await fetch(`${CONFIG.API_URL}/api/tasks/execute`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ task_id: taskId })
        });
        
        if (response.ok) {
            const result = await response.json();
            
            // 构建AI回复内容
            let botMessage = '';
            if (result.success) {
                botMessage = `✅ **任务执行成功**\n\n**任务**: ${taskName}\n**状态**: 已完成\n\n**执行结果**:\n${result.result || '执行完成，无详细输出'}`;
                showNotification('任务执行成功', 'success');
            } else {
                botMessage = `❌ **任务执行失败**\n\n**任务**: ${taskName}\n**状态**: 失败\n\n**错误信息**:\n${result.error || '未知错误'}`;
                showNotification('执行失败: ' + result.error, 'error');
            }
            
            // 添加AI回复到聊天界面和历史
            addMessageToUI('assistant', botMessage, true, false, []);
            messageHistory.push({ role: 'assistant', content: botMessage });
            saveChatToHistory();
            
            // 刷新任务状态面板
            setTimeout(() => refreshTaskStatus(), 1000);
        } else {
            const errorMsg = `❌ **任务执行请求失败**\n\nHTTP错误: ${response.status}`;
            addMessageToUI('assistant', errorMsg, true, false, []);
            messageHistory.push({ role: 'assistant', content: errorMsg });
            saveChatToHistory();
            showNotification('请求失败', 'error');
        }
    } catch (error) {
        console.error('[executeTaskNow] 执行失败:', error);
        const errorMsg = `❌ **任务执行异常**\n\n${error.message}`;
        addMessageToUI('assistant', errorMsg, true, false, []);
        messageHistory.push({ role: 'assistant', content: errorMsg });
        saveChatToHistory();
        showNotification('执行异常: ' + error.message, 'error');
    }
}

// 任务状态栏载入/刷新时优先使用当前会话快照
function syncTaskStatusBarForActiveSession(sessionId = currentSession || currentChatId, options = {}) {
    if (!sessionId) {
        renderTaskStatusBar({ root_task: {}, child_tasks: [] }, { emptyMessage: options.emptyMessage || '暂无激活会话任务。' });
        return Promise.resolve(null);
    }
    return refreshTaskStatusBar(sessionId, options);
}

// 定期刷新任务状态
function startTaskStatusRefresh() {
    // 立即执行一次
    refreshTaskStatus();
    ensureTaskStatusBar();
    syncTaskStatusBarForActiveSession().catch((error) => {
        console.warn('[startTaskStatusRefresh] Failed to initialize task status bar:', error);
    });
    
    // 每30秒刷新一次
    setInterval(refreshTaskStatus, 30000);
}

// 刷新任务状态（不打开面板）
async function refreshTaskStatus() {
    try {
        const response = await fetch(`${CONFIG.API_URL}/api/tasks/status`, {
            method: 'GET',
            headers: { 'Content-Type': 'application/json' }
        }).catch(() => null);
        
        let data;
        if (response && response.ok) {
            data = await response.json();
        } else {
            data = getLocalTaskData();
        }
        
        taskDataCache = data;
        updateTaskBadge(data);
        
        // 如果面板打开，刷新显示
        if (taskPanelOpen) {
            renderTaskPanel(data);
        }

        // 任务状态栏也跟随刷新（不影响调度器面板）
        syncTaskStatusBarForActiveSession(currentSession || currentChatId, { force: true }).catch((error) => {
            console.warn('[refreshTaskStatus] task status bar refresh skipped:', error);
        });
        syncFileChangeSummaryForActiveSession(currentSession || currentChatId, {
            expanded: fileChangeSummaryState.expanded,
        }).catch((error) => {
            console.warn('[refreshTaskStatus] file change summary refresh skipped:', error);
        });
        
        // 保存到localStorage
        localStorage.setItem('nanobot_tasks_status', JSON.stringify(data));
        
    } catch (error) {
        console.error('[refreshTaskStatus] 刷新失败:', error);
    }
}

// 页面加载时启动任务状态刷新
document.addEventListener('DOMContentLoaded', function() {
    startTaskStatusRefresh();
});

// ========== 面板拖拽和右键菜单功能 ==========

// 使面板可拖拽
function makePanelDraggable(panel, headerSelector) {
    const header = panel.querySelector(headerSelector);
    if (!header) return;
    
    let isDragging = false;
    let startX, startY, startLeft, startTop;
    
    // 移除 transform 居中，改用绝对定位
    const rect = panel.getBoundingClientRect();
    panel.style.position = 'fixed';
    panel.style.left = rect.left + 'px';
    panel.style.top = rect.top + 'px';
    panel.style.transform = 'none';
    panel.style.margin = '0';
    
    header.addEventListener('mousedown', (e) => {
        // 忽略按钮点击
        if (e.target.closest('button')) return;
        
        isDragging = true;
        startX = e.clientX;
        startY = e.clientY;
        startLeft = parseInt(panel.style.left) || rect.left;
        startTop = parseInt(panel.style.top) || rect.top;
        
        panel.style.transition = 'none';
        e.preventDefault();
    });
    
    document.addEventListener('mousemove', (e) => {
        if (!isDragging) return;
        
        const dx = e.clientX - startX;
        const dy = e.clientY - startY;
        
        let newLeft = startLeft + dx;
        let newTop = startTop + dy;
        
        // 限制在窗口内
        const panelRect = panel.getBoundingClientRect();
        newLeft = Math.max(0, Math.min(newLeft, window.innerWidth - panelRect.width));
        newTop = Math.max(0, Math.min(newTop, window.innerHeight - panelRect.height));
        
        panel.style.left = newLeft + 'px';
        panel.style.top = newTop + 'px';
    });
    
    document.addEventListener('mouseup', () => {
        isDragging = false;
        panel.style.transition = '';
    });
}

// 面板右键菜单
function makePanelContextMenu(panel) {
    panel.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        showPanelContextMenu(e.clientX, e.clientY, panel);
    });
}

// 显示面板右键菜单
function showPanelContextMenu(x, y, panel) {
    // 移除已有菜单
    const existingMenu = document.getElementById('panel-context-menu');
    if (existingMenu) existingMenu.remove();
    
    const menu = document.createElement('div');
    menu.id = 'panel-context-menu';
    menu.style.cssText = `
        position: fixed;
        left: ${x}px;
        top: ${y}px;
        background: rgba(30, 41, 59, 0.98);
        border: 1px solid rgba(99, 102, 241, 0.4);
        border-radius: 8px;
        padding: 6px 0;
        min-width: 180px;
        z-index: 2147483647;
        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
        font-size: 13px;
        color: #e2e8f0;
    `;
    
    const menuItems = [
        { label: '最大化', icon: 'fa-expand', shortcut: '(X)', action: () => maximizePanel(panel) },
        { label: '最小化', icon: 'fa-compress', shortcut: '(N)', action: () => minimizePanel(panel) },
        { label: '全屏', icon: 'fa-arrows-alt', shortcut: '(F)', action: () => fullscreenPanel(panel) },
        { type: 'divider' },
        { label: '移动', icon: 'fa-arrows', shortcut: '(M)', action: () => startMoveMode(panel) },
        { label: '改变大小', icon: 'fa-expand-arrows-alt', shortcut: '(R)', action: () => startResizeMode(panel) },
        { label: '居中显示', icon: 'fa-crosshairs', action: () => centerPanel(panel) },
        { type: 'divider' },
        { label: '总是置顶', icon: 'fa-thumbtack', shortcut: '(T)', action: () => toggleAlwaysOnTop(panel), toggle: true },
        { label: '移至左屏', icon: 'fa-arrow-left', action: () => movePanelToLeft(panel) },
        { label: '移至右屏', icon: 'fa-arrow-right', action: () => movePanelToRight(panel) },
        { type: 'divider' },
        { label: '关闭', icon: 'fa-times', shortcut: '(C)', action: () => closePanel(panel), danger: true }
    ];
    
    menuItems.forEach(item => {
        if (item.type === 'divider') {
            const divider = document.createElement('div');
            divider.style.cssText = 'height: 1px; background: rgba(99, 102, 241, 0.2); margin: 4px 8px;';
            menu.appendChild(divider);
        } else {
            const menuItem = document.createElement('div');
            menuItem.style.cssText = `
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 10px;
                padding: 8px 14px;
                cursor: pointer;
                transition: background 0.15s;
                ${item.danger ? 'color: #f87171;' : ''}
            `;
            const labelSpan = document.createElement('span');
            labelSpan.innerHTML = `<i class="fas ${item.icon}" style="width: 16px;"></i> ${item.label}`;
            if (item.shortcut) {
                const shortcutSpan = document.createElement('span');
                shortcutSpan.textContent = item.shortcut;
                shortcutSpan.style.cssText = 'color: rgba(148, 163, 184, 0.6); font-size: 11px;';
                menuItem.appendChild(labelSpan);
                menuItem.appendChild(shortcutSpan);
            } else {
                menuItem.appendChild(labelSpan);
            }
            menuItem.onmouseenter = () => menuItem.style.background = 'rgba(99, 102, 241, 0.2)';
            menuItem.onmouseleave = () => menuItem.style.background = '';
            menuItem.onclick = () => {
                item.action();
                menu.remove();
            };
            menu.appendChild(menuItem);
        }
    });
    
    document.body.appendChild(menu);
    
    // 点击外部关闭
    const closeMenu = (e) => {
        if (!menu.contains(e.target)) {
            menu.remove();
            document.removeEventListener('click', closeMenu);
        }
    };
    setTimeout(() => document.addEventListener('click', closeMenu), 10);
}

// 最大化面板
function maximizePanel(panel) {
    if (panel?.id === 'taskStatusBar') {
        refreshTaskStatusBarPopup('maximized', true);
        return;
    }
    panel.style.transition = 'all 0.2s ease';
    panel.style.left = '20px';
    panel.style.top = '20px';
    panel.style.right = 'auto';
    panel.style.bottom = 'auto';
    panel.style.transform = 'none';
    panel.style.width = 'calc(100vw - 40px)';
    panel.style.height = 'calc(100vh - 40px)';
    panel.style.maxWidth = 'calc(100vw - 40px)';
    panel.style.maxHeight = 'calc(100vh - 40px)';
}

// 最小化面板（恢复默认大小）
function minimizePanel(panel) {
    if (panel?.id === 'taskStatusBar') {
        refreshTaskStatusBarPopup('default', false);
        return;
    }
    panel.style.transition = 'all 0.2s ease';
    if (panel.id === 'taskStatusBar') {
        taskStatusBarBodyExpanded = false;
    }
    const defaultWidth = panel.dataset.defaultWidth || (panel.id === 'fix-panel' ? '700px' : '600px');
    panel.style.width = defaultWidth;
    panel.style.height = panel.dataset.defaultHeight || '';
    panel.style.maxWidth = panel.dataset.defaultMaxWidth || '95vw';
    panel.style.maxHeight = panel.dataset.defaultMaxHeight || 'calc(100vh - 40px)';
    panel.style.borderRadius = '';
    panel.style.left = panel.dataset.defaultLeft || '50%';
    panel.style.top = panel.dataset.defaultTop || '10px';
    panel.style.right = panel.dataset.defaultRight || 'auto';
    panel.style.bottom = panel.dataset.defaultBottom || 'auto';
    panel.style.transform = panel.dataset.defaultTransform || 'translateX(-50%)';
}

// 居中面板
function centerPanel(panel) {
    if (panel?.id === 'taskStatusBar') {
        refreshTaskStatusBarPopup('centered', true);
        return;
    }
    panel.style.transition = 'all 0.2s ease';
    const width = panel.offsetWidth;
    const height = panel.offsetHeight;
    panel.style.right = 'auto';
    panel.style.bottom = 'auto';
    panel.style.transform = 'none';
    panel.style.left = (window.innerWidth - width) / 2 + 'px';
    panel.style.top = (window.innerHeight - height) / 2 + 'px';
}

// 开始移动模式（高亮标题栏）
function startMoveMode(panel) {
    if (panel?.id === 'taskStatusBar') {
        refreshTaskStatusBarPopup('custom', true);
        const host = document.getElementById('taskStatusBar');
        const body = getTaskStatusBarPopupBody(host);
        if (!body) return;
        body.style.cursor = 'move';
        const startDrag = (e) => {
            if (e.button !== 0) return;
            if (e.target.closest?.('button, a, input, textarea, select, .task-status-bar__tree-toggle, .task-status-bar__status-pill, .task-status-bar__copy-task-id, .task-status-bar__copy-root-task')) return;
            const rect = body.getBoundingClientRect();
            const offsetX = e.clientX - rect.left;
            const offsetY = e.clientY - rect.top;
            const onMove = (moveEvent) => {
                taskStatusBarPopupMode = 'custom';
                const nextLeft = Math.max(8, moveEvent.clientX - offsetX);
                const nextTop = Math.max(8, moveEvent.clientY - offsetY);
                body.style.left = `${nextLeft}px`;
                body.style.top = `${nextTop}px`;
                body.style.right = 'auto';
                taskStatusBarCustomLeft = `${nextLeft}px`;
                taskStatusBarCustomTop = `${nextTop}px`;
                body.dataset.taskStatusBarPopupLeft = `${nextLeft}px`;
                body.dataset.taskStatusBarPopupTop = `${nextTop}px`;
            };
            const onUp = () => {
                document.removeEventListener('mousemove', onMove);
                document.removeEventListener('mouseup', onUp);
                body.style.cursor = '';
            };
            document.addEventListener('mousemove', onMove);
            document.addEventListener('mouseup', onUp);
            e.preventDefault();
        };
        body.onmousedown = startDrag;
        return;
    }
    const header = panel.querySelector('.fix-panel-header, .task-panel-header, .change-set-drag-bar');
    if (header) {
        header.style.background = 'linear-gradient(135deg, rgba(99, 102, 241, 0.4) 0%, rgba(139, 92, 246, 0.3) 100%)';
        setTimeout(() => {
            header.style.background = '';
        }, 2000);
    }
}

// 关闭面板
function closePanel(panel) {
    if (panel.id === 'fix-panel') {
        closeFixPanel();
    } else if (panel.id === 'taskStatusBar') {
        taskStatusBarBodyExpanded = false;
        taskStatusBarPopupMode = 'default';
        taskStatusBarCustomLeft = '';
        taskStatusBarCustomTop = '';
        taskStatusBarCustomWidth = '';
        taskStatusBarCustomHeight = '';
        taskStatusBarAlwaysOnTop = false;
        deactivateShortcutPanel(panel);
        renderTaskStatusBar(taskStatusBarCache || loadTaskStatusBarCache(currentSession || currentChatId) || { root_task: {}, child_tasks: [] }, { emptyMessage: '当前会话任务已更新。' });
    } else if (panel.closest('#task-panel-overlay')) {
        closeTaskSchedulerPanel();
    } else {
        panel.remove();
    }
}

// 全屏面板
function fullscreenPanel(panel) {
    if (panel?.id === 'taskStatusBar') {
        refreshTaskStatusBarPopup('fullscreen', true);
        return;
    }
    panel.style.transition = 'all 0.2s ease';
    panel.style.left = '0';
    panel.style.top = '0';
    panel.style.right = 'auto';
    panel.style.bottom = 'auto';
    panel.style.transform = 'none';
    panel.style.width = '100vw';
    panel.style.height = '100vh';
    panel.style.maxWidth = '100vw';
    panel.style.maxHeight = '100vh';
    panel.style.borderRadius = '0';
}

// 开始调整大小模式
function startResizeMode(panel) {
    if (panel?.id === 'taskStatusBar') {
        refreshTaskStatusBarPopup('custom', true);
        const host = document.getElementById('taskStatusBar');
        const body = getTaskStatusBarPopupBody(host);
        if (!body) return;
        let handle = body.querySelector('.task-status-bar__resize-handle');
        if (!handle) {
            handle = document.createElement('div');
            handle.className = 'task-status-bar__resize-handle';
            handle.style.cssText = `
                position: absolute;
                right: 0;
                bottom: 0;
                width: 18px;
                height: 18px;
                cursor: se-resize;
                background: linear-gradient(135deg, transparent 50%, rgba(99, 102, 241, 0.55) 50%);
                border-radius: 0 0 14px 0;
            `;
            body.appendChild(handle);
        }
        handle.onmousedown = (e) => {
            if (e.button !== 0) return;
            const startRect = body.getBoundingClientRect();
            const startX = e.clientX;
            const startY = e.clientY;
            const startWidth = startRect.width;
            const startHeight = startRect.height;
            const onMove = (moveEvent) => {
                const nextWidth = Math.max(320, startWidth + (moveEvent.clientX - startX));
                const nextHeight = Math.max(220, startHeight + (moveEvent.clientY - startY));
                body.style.width = `${nextWidth}px`;
                body.style.height = `${nextHeight}px`;
                taskStatusBarCustomWidth = `${nextWidth}px`;
                taskStatusBarCustomHeight = `${nextHeight}px`;
                body.dataset.taskStatusBarPopupWidth = `${nextWidth}px`;
                body.dataset.taskStatusBarPopupHeight = `${nextHeight}px`;
                taskStatusBarPopupMode = 'custom';
            };
            const onUp = () => {
                document.removeEventListener('mousemove', onMove);
                document.removeEventListener('mouseup', onUp);
            };
            document.addEventListener('mousemove', onMove);
            document.addEventListener('mouseup', onUp);
            e.preventDefault();
            e.stopPropagation();
        };
        showNotification('拖动右下角可调整任务状态栏大小', 'info');
        return;
    }
    // 添加调整大小提示
    const resizeHandle = document.createElement('div');
    resizeHandle.style.cssText = `
        position: absolute;
        right: 0;
        bottom: 0;
        width: 20px;
        height: 20px;
        cursor: se-resize;
        background: linear-gradient(135deg, transparent 50%, rgba(99, 102, 241, 0.5) 50%);
        border-radius: 0 0 8px 0;
    `;
    panel.style.position = 'relative';
    panel.appendChild(resizeHandle);
    
    let isResizing = false;
    let startX, startY, startWidth, startHeight;
    
    resizeHandle.addEventListener('mousedown', (e) => {
        isResizing = true;
        startX = e.clientX;
        startY = e.clientY;
        startWidth = panel.offsetWidth;
        startHeight = panel.offsetHeight;
        e.preventDefault();
    });
    
    const doResize = (e) => {
        if (!isResizing) return;
        const newWidth = startWidth + (e.clientX - startX);
        const newHeight = startHeight + (e.clientY - startY);
        panel.style.width = Math.max(300, newWidth) + 'px';
        panel.style.height = Math.max(200, newHeight) + 'px';
    };
    
    const stopResize = () => {
        isResizing = false;
    };
    
    document.addEventListener('mousemove', doResize);
    document.addEventListener('mouseup', stopResize);
    
    // 高亮提示
    panel.style.boxShadow = '0 0 20px rgba(99, 102, 241, 0.6)';
    setTimeout(() => {
        panel.style.boxShadow = '';
    }, 2000);
}

// 切换总是置顶
let alwaysOnTopPanel = null;
function toggleAlwaysOnTop(panel) {
    if (panel?.id === 'taskStatusBar') {
        const host = document.getElementById('taskStatusBar');
        const body = getTaskStatusBarPopupBody(host);
        if (!body) return;
        taskStatusBarAlwaysOnTop = !taskStatusBarAlwaysOnTop;
        body.dataset.taskStatusBarAlwaysOnTop = taskStatusBarAlwaysOnTop ? 'true' : 'false';
        body.style.zIndex = taskStatusBarAlwaysOnTop ? '2147483002' : '2147483001';
        showNotification(taskStatusBarAlwaysOnTop ? '已置顶显示' : '已取消置顶', 'info');
        return;
    }
    if (alwaysOnTopPanel === panel) {
        // 取消置顶
        panel.style.zIndex = '10000';
        alwaysOnTopPanel = null;
        showNotification('已取消置顶', 'info');
    } else {
        // 置顶
        panel.style.zIndex = '99999';
        alwaysOnTopPanel = panel;
        showNotification('已置顶显示', 'success');
    }
}

// 移至左屏
function movePanelToLeft(panel) {
    if (panel?.id === 'taskStatusBar') {
        refreshTaskStatusBarPopup('left', true);
        return;
    }
    panel.style.transition = 'all 0.2s ease';
    panel.style.left = '10px';
    panel.style.top = '10px';
    panel.style.right = 'auto';
    panel.style.bottom = 'auto';
    panel.style.transform = 'none';
    panel.style.height = 'calc(100vh - 20px)';
}

// 移至右屏
function movePanelToRight(panel) {
    if (panel?.id === 'taskStatusBar') {
        refreshTaskStatusBarPopup('right', true);
        return;
    }
    panel.style.transition = 'all 0.2s ease';
    const width = panel.offsetWidth;
    panel.style.left = (window.innerWidth - width - 10) + 'px';
    panel.style.top = '10px';
    panel.style.right = 'auto';
    panel.style.bottom = 'auto';
    panel.style.transform = 'none';
    panel.style.height = 'calc(100vh - 20px)';
}

// ========== 面板快捷键功能 ==========

let panelShortcutsHandler = null;
let activeShortcutPanel = null;
let shortcutPanelOutsideClickBound = false;

function activateShortcutPanel(panel) {
    if (!panel) return;
    if (!panel.hasAttribute('tabindex')) {
        panel.tabIndex = -1;
    }
    if (activeShortcutPanel && activeShortcutPanel !== panel) {
        activeShortcutPanel.dataset.keyboardActive = 'false';
        activeShortcutPanel.style.outline = '';
        if (alwaysOnTopPanel !== activeShortcutPanel) {
            activeShortcutPanel.style.zIndex = activeShortcutPanel.id === 'taskStatusBar' ? '2147483000' : '10000';
        }
    }
    activeShortcutPanel = panel;
    panel.dataset.keyboardActive = 'true';
    panel.style.outline = '2px solid rgba(96, 165, 250, 0.75)';
    panel.style.outlineOffset = '2px';
    panel.style.zIndex = panel.id === 'taskStatusBar' ? '2147483000' : '99999';
    panel.focus({ preventScroll: true });
}

function deactivateShortcutPanel(panel = activeShortcutPanel) {
    if (!panel) return;
    panel.dataset.keyboardActive = 'false';
    panel.style.outline = '';
    if (alwaysOnTopPanel !== panel) {
        panel.style.zIndex = panel.id === 'taskStatusBar' ? '2147483000' : '10000';
    }
    if (activeShortcutPanel === panel) {
        activeShortcutPanel = null;
    }
}

function bindShortcutPanelOutsideClick() {
    if (shortcutPanelOutsideClickBound) return;
    document.addEventListener('mousedown', (e) => {
        if (!activeShortcutPanel) return;
        if (activeShortcutPanel.contains(e.target)) return;
        // For taskStatusBar: the expanded body is portaled to document.body,
        // so it's not inside the host. Check if click is on the portaled body.
        if (activeShortcutPanel.id === 'taskStatusBar') {
            const portaledBody = document.querySelector('body > .task-status-bar__body.task-status-bar__body--expanded');
            if (portaledBody && portaledBody.contains(e.target)) return;
        }
        deactivateShortcutPanel(activeShortcutPanel);
    }, true);
    shortcutPanelOutsideClickBound = true;
}

function isEditableKeyboardTarget(target) {
    if (!target) return false;
    const tag = String(target.tagName || '').toLowerCase();
    return tag === 'input' || tag === 'textarea' || tag === 'select' || target.isContentEditable;
}

function nudgePanel(panel, dx, dy) {
    const rect = panel.getBoundingClientRect();
    const parent = panel.offsetParent || panel.parentElement || document.body;
    const parentRect = parent.getBoundingClientRect();
    const width = panel.offsetWidth || rect.width || 520;
    const height = panel.offsetHeight || rect.height || 240;
    const currentLeft = rect.left - parentRect.left + parent.scrollLeft;
    const currentTop = rect.top - parentRect.top + parent.scrollTop;
    const maxLeft = Math.max(0, parent.clientWidth - width);
    const maxTop = Math.max(0, parent.scrollHeight - height);
    const nextLeft = Math.max(0, Math.min(currentLeft + dx, maxLeft));
    const nextTop = Math.max(0, Math.min(currentTop + dy, maxTop));
    panel.dataset.userPositioned = 'true';
    panel.style.transition = 'none';
    panel.style.left = `${nextLeft}px`;
    panel.style.top = `${nextTop}px`;
    panel.style.right = 'auto';
    panel.style.bottom = 'auto';
    panel.style.transform = 'none';
}

function movePanelToScopedEdge(panel, horizontal, vertical) {
    const rect = panel.getBoundingClientRect();
    const parent = panel.offsetParent || panel.parentElement || document.body;
    const parentRect = parent.getBoundingClientRect();
    const width = panel.offsetWidth || rect.width || 520;
    const height = panel.offsetHeight || rect.height || 240;
    const currentLeft = rect.left - parentRect.left + parent.scrollLeft;
    const currentTop = rect.top - parentRect.top + parent.scrollTop;
    const maxLeft = Math.max(0, parent.clientWidth - width);
    const maxTop = Math.max(0, parent.scrollHeight - height);
    const nextLeft = horizontal === 'left' ? 0 : horizontal === 'right' ? maxLeft : currentLeft;
    const nextTop = vertical === 'top' ? 0 : vertical === 'bottom' ? maxTop : currentTop;
    panel.dataset.userPositioned = 'true';
    panel.style.transition = 'none';
    panel.style.left = `${Math.max(0, Math.min(nextLeft, maxLeft))}px`;
    panel.style.top = `${Math.max(0, Math.min(nextTop, maxTop))}px`;
    panel.style.right = 'auto';
    panel.style.bottom = 'auto';
    panel.style.transform = 'none';
}

// 启用面板快捷键
function enablePanelShortcuts(panel, panelType) {
    bindShortcutPanelOutsideClick();
    // 移除旧的快捷键监听
    if (panelShortcutsHandler) {
        document.removeEventListener('keydown', panelShortcutsHandler);
    }
    
    // 创建新的快捷键处理器
    panelShortcutsHandler = (e) => {
        // 检查面板是否还存在
        if (!document.body.contains(panel)) {
            document.removeEventListener('keydown', panelShortcutsHandler);
            panelShortcutsHandler = null;
            return;
        }
        if (isEditableKeyboardTarget(e.target)) return;
        if (activeShortcutPanel !== panel || panel.dataset.keyboardActive !== 'true') return;
        
        // ESC - 关闭面板
        if (e.key === 'Escape') {
            e.preventDefault();
            closePanel(panel);
            document.removeEventListener('keydown', panelShortcutsHandler);
            panelShortcutsHandler = null;
            return;
        }
        
        // F - 全屏
        if (e.key === 'f' || e.key === 'F') {
            e.preventDefault();
            fullscreenPanel(panel);
            return;
        }
        
        // X - 最大化
        if (e.key === 'x' || e.key === 'X') {
            e.preventDefault();
            maximizePanel(panel);
            return;
        }
        
        // N - 最小化/恢复
        if (e.key === 'n' || e.key === 'N') {
            e.preventDefault();
            minimizePanel(panel);
            return;
        }
        
        // M - 移动模式
        if (e.key === 'm' || e.key === 'M') {
            e.preventDefault();
            startMoveMode(panel);
            return;
        }
        
        // R - 调整大小
        if (e.key === 'r' || e.key === 'R') {
            e.preventDefault();
            startResizeMode(panel);
            return;
        }
        
        // T - 置顶
        if (e.key === 't' || e.key === 'T') {
            e.preventDefault();
            toggleAlwaysOnTop(panel);
            return;
        }

        if (e.ctrlKey && e.key === 'Home') {
            e.preventDefault();
            movePanelToScopedEdge(panel, 'left');
            return;
        }

        if (e.ctrlKey && e.key === 'End') {
            e.preventDefault();
            movePanelToScopedEdge(panel, 'right');
            return;
        }

        if (e.ctrlKey && e.key === 'PageUp') {
            e.preventDefault();
            movePanelToScopedEdge(panel, null, 'top');
            return;
        }

        if (e.ctrlKey && e.key === 'PageDown') {
            e.preventDefault();
            movePanelToScopedEdge(panel, null, 'bottom');
            return;
        }
        
        // 左箭头 - 向左微调（Shift+箭头保留原生文字选择）
        if (e.key === 'ArrowLeft' && !e.shiftKey) {
            e.preventDefault();
            nudgePanel(panel, -16, 0);
            return;
        }
        
        // 右箭头 - 向右微调
        if (e.key === 'ArrowRight' && !e.shiftKey) {
            e.preventDefault();
            nudgePanel(panel, 16, 0);
            return;
        }
        
        // 上箭头 - 向上微调
        if (e.key === 'ArrowUp' && !e.shiftKey) {
            e.preventDefault();
            nudgePanel(panel, 0, -16);
            return;
        }

        // 下箭头 - 向下微调
        if (e.key === 'ArrowDown' && !e.shiftKey) {
            e.preventDefault();
            nudgePanel(panel, 0, 16);
            return;
        }
    };
    
    document.addEventListener('keydown', panelShortcutsHandler);
    
    // 面板关闭时自动移除监听
    const observer = new MutationObserver(() => {
        if (!document.body.contains(panel)) {
            document.removeEventListener('keydown', panelShortcutsHandler);
            panelShortcutsHandler = null;
            observer.disconnect();
        }
    });
    observer.observe(document.body, { childList: true, subtree: true });
}

// ========== P1-1: Token Status Bar ==========
function formatTokenCount(n) {
    if (!n || !Number.isFinite(n) || n <= 0) return '--';
    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
    if (n >= 1000) return (n / 1000).toFixed(1) + 'k';
    return String(n);
}

function updateTokenStatusBar(stats) {
    if (!stats) return;
    const bar = document.getElementById('tokenStatusBar');
    if (!bar) return;

    const turnTokens = Number(stats.total_tokens || 0) || (Number(stats.prompt_tokens || 0) + Number(stats.completion_tokens || 0));
    const speed = Number(stats.tokens_per_second || 0);
    const model = stats.model_display || stats.model || '';

    if (turnTokens > 0) {
        lastTurnTokens = turnTokens;
        sessionTotalTokens += turnTokens;
    }
    if (speed > 0) lastTurnSpeed = speed;
    if (model) lastTurnModel = model;

    document.getElementById('tokenBarTurnValue').textContent = formatTokenCount(lastTurnTokens);
    document.getElementById('tokenBarSessionValue').textContent = formatTokenCount(sessionTotalTokens);
    document.getElementById('tokenBarSpeedValue').textContent = lastTurnSpeed > 0 ? lastTurnSpeed.toFixed(1) + ' t/s' : '--';
    document.getElementById('tokenBarModelValue').textContent = lastTurnModel || '--';

    bar.classList.add('visible');
}

function resetTokenStatusBar() {
    sessionTotalTokens = 0;
    lastTurnTokens = 0;
    lastTurnSpeed = 0;
    lastTurnModel = '';
    const bar = document.getElementById('tokenStatusBar');
    if (bar) bar.classList.remove('visible');
}

// ========== P1-2: Sidebar Task Progress Panel ==========
const TASK_STATE_ICONS = {
    created: '⏳', planned: '📋', in_progress: '🔄',
    waiting_approval: '⏸️', verifying: '🔍', completed: '✅',
    failed: '❌', blocked: '🚫', cancelled: '🗑️',
};

function updateSessionTask(data) {
    if (!data) return;
    const rootTask = data.root_task || data;
    const childTasks = data.child_tasks || [];
    if (rootTask && rootTask.id) {
        sessionTaskMap[rootTask.id] = {
            id: rootTask.id,
            parent_id: null,
            title: rootTask.title || rootTask.objective || '任务',
            state: rootTask.state || rootTask.status || 'created',
        };
    }
    for (const child of childTasks) {
        if (child && child.id) {
            sessionTaskMap[child.id] = {
                id: child.id,
                parent_id: child.parent_id || (rootTask && rootTask.id) || null,
                title: child.title || child.objective || '子任务',
                state: child.state || child.status || 'created',
            };
        }
    }
    renderSidebarTaskProgress();
}

function renderSidebarTaskProgress() {
    const container = document.getElementById('sidebarTaskProgress');
    if (!container) return;

    const tasks = Object.values(sessionTaskMap);
    const activeCount = tasks.filter(t => !['completed', 'failed', 'cancelled'].includes(t.state)).length;

    const summaryEl = container.querySelector('summary');
    if (summaryEl) {
        const label = summaryEl.querySelector('.task-progress-label');
        if (label) label.textContent = `当前任务 (${activeCount})`;
    }

    const listEl = container.querySelector('.sidebar-task-progress__list');
    if (!listEl) return;

    if (tasks.length === 0) {
        listEl.innerHTML = '<div class="sidebar-task-progress__empty">暂无活跃任务</div>';
        return;
    }

    // Render: roots first, then children
    const roots = tasks.filter(t => !t.parent_id);
    const children = tasks.filter(t => t.parent_id);

    let html = '';
    for (const t of roots) {
        const icon = TASK_STATE_ICONS[t.state] || '⬜';
        html += `<div class="sidebar-task-progress__item" title="${t.state}">
            <span class="sidebar-task-progress__icon">${icon}</span>
            <span class="sidebar-task-progress__title">${escapeHtml(t.title)}</span>
        </div>`;
        // Render children of this root
        for (const c of children.filter(ch => ch.parent_id === t.id)) {
            const cIcon = TASK_STATE_ICONS[c.state] || '⬜';
            html += `<div class="sidebar-task-progress__item sidebar-task-progress__item--child" title="${c.state}">
                <span class="sidebar-task-progress__icon">${cIcon}</span>
                <span class="sidebar-task-progress__title">${escapeHtml(c.title)}</span>
            </div>`;
        }
    }
    // Orphan children (parent not in map)
    const rootIds = new Set(roots.map(r => r.id));
    for (const c of children.filter(ch => !rootIds.has(ch.parent_id))) {
        const cIcon = TASK_STATE_ICONS[c.state] || '⬜';
        html += `<div class="sidebar-task-progress__item sidebar-task-progress__item--child" title="${c.state}">
            <span class="sidebar-task-progress__icon">${cIcon}</span>
            <span class="sidebar-task-progress__title">${escapeHtml(c.title)}</span>
        </div>`;
    }

    listEl.innerHTML = html;
}

function resetSidebarTaskProgress() {
    sessionTaskMap = {};
    renderSidebarTaskProgress();
}

// ========== P1-3: Tool Result Card Builder ==========
function buildToolResultCardHtml(toolName, success, elapsedMs, output, isDiff) {
    const statusClass = success ? 'tool-result-card--success' : 'tool-result-card--fail';
    const icon = success ? '✅' : '❌';
    // Collapse consecutive blank lines — marked.js terminates HTML blocks at blank lines
    const safeOutput = output ? output.replace(/\n{2,}/g, '\n') : '';
    const lineCount = safeOutput ? safeOutput.split('\n').length : 0;
    const expandByDefault = lineCount <= 20;
    const openAttr = expandByDefault ? ' open' : '';
    const timeStr = elapsedMs > 0 ? `${elapsedMs}ms` : '';

    let bodyContent = '';
    if (safeOutput) {
        if (isDiff) {
            bodyContent = colorDiffOutput(safeOutput);
        } else {
            bodyContent = `<pre>${escapeHtml(safeOutput)}</pre>`;
        }
    }

    return `<details class="tool-result-card ${statusClass}"${openAttr}><summary class="tool-result-card__header"><span class="tool-result-card__icon">${icon}</span><span class="tool-result-card__name">${escapeHtml(toolName)}</span>${timeStr ? `<span class="tool-result-card__time">${timeStr}</span>` : ''}<span class="tool-result-card__chevron"><i class="fas fa-chevron-right"></i></span></summary>${bodyContent ? `<div class="tool-result-card__body">${bodyContent}</div>` : ''}</details>`;
}

function colorDiffOutput(text) {
    const lines = text.split('\n');
    const colored = lines.map(line => {
        if (line.startsWith('@@')) return `<span class="diff-hunk">${escapeHtml(line)}</span>`;
        if (line.startsWith('+')) return `<span class="diff-add">${escapeHtml(line)}</span>`;
        if (line.startsWith('-')) return `<span class="diff-del">${escapeHtml(line)}</span>`;
        return escapeHtml(line);
    }).join('\n');
    return `<pre>${colored}</pre>`;
}

// escapeHtml already defined above — reused by P1 functions
