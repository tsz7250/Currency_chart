import time
from threading import Lock

# LRU Cache 類別
class LRUCache:
    def __init__(self, capacity, ttl_seconds=3600):
        """
        LRU Cache 實現
        capacity: 快取容量
        ttl_seconds: 過期時間（秒），預設1小時
        """
        self.capacity = capacity
        self.ttl_seconds = ttl_seconds
        self.cache = {}  # key -> {'value': value, 'timestamp': timestamp, 'ttl': ttl, 'is_pinned': bool}
        self.access_order = []  # 存儲存取順序
        self.pinned_keys = set() # 存儲不應被淘汰的鍵
        self.lock = Lock()

        # 統計資訊
        self._total_requests = 0
        self._cache_hits = 0

    def get(self, key):
        """獲取快取值"""
        with self.lock:
            self._total_requests += 1

            if key not in self.cache:
                return None

            # 檢查是否過期
            entry = self.cache[key]
            current_time = time.time()
            # 如果 ttl 為 None，表示永不過期
            if entry.get('ttl') is not None and current_time - entry['timestamp'] > entry['ttl']:
                # 過期，移除
                self._remove_key(key)
                return None

            # 命中快取
            self._cache_hits += 1

            # 更新存取順序（移到最前面）
            self.access_order.remove(key)
            self.access_order.append(key)

            return entry['value']

    def put(self, key, value, ttl=None, is_pinned=False):
        """設定快取值，ttl=None 表示使用默認 TTL，ttl=False 表示永不過期，is_pinned=True 表示永不淘汰"""
        with self.lock:
            current_time = time.time()

            if ttl is False:
                actual_ttl = None
            elif ttl is None:
                actual_ttl = self.ttl_seconds
            else:
                actual_ttl = ttl

            if is_pinned:
                self.pinned_keys.add(key)
            else:
                self.pinned_keys.discard(key) # 如果之前是固定的，現在不是了，就移除

            if key in self.cache:
                # 更新現有項目
                self.cache[key] = {
                    'value': value,
                    'timestamp': current_time,
                    'ttl': actual_ttl,
                    'is_pinned': is_pinned
                }
                # 更新存取順序
                if key in self.access_order:
                    self.access_order.remove(key)
                self.access_order.append(key)
            else:
                # 新增項目
                # 檢查容量（但永不過期的項目不會被 LRU 淘汰）
                if len(self.cache) >= self.capacity:
                    # 找出最久未使用且可淘汰的項目
                    self._evict_lru_item()

                self.cache[key] = {
                    'value': value,
                    'timestamp': current_time,
                    'ttl': actual_ttl,
                    'is_pinned': is_pinned
                }
                self.access_order.append(key)

    def _evict_lru_item(self):
        """淘汰最久未使用的項目（但跳過永不過期或被固定的項目）"""
        for key in list(self.access_order): # 遍歷副本以允許修改原列表
            entry = self.cache.get(key)
            if entry and not entry.get('is_pinned', False) and entry.get('ttl') is not None:
                self._remove_key(key)
                return
        # 如果所有項目都是永不過期或被固定的，或者沒有可淘汰的項目，則不執行任何操作
        # 這裡不需要額外的處理，因為如果所有項目都是固定的，就不應該淘汰

    def _remove_key(self, key):
        """移除指定的鍵（內部方法，不加鎖）"""
        if key in self.cache:
            del self.cache[key]
            if key in self.access_order:
                self.access_order.remove(key)
            self.pinned_keys.discard(key) # 確保從固定鍵集合中移除

    def clear_expired(self):
        """清理過期項目（跳過永不過期的項目）"""
        with self.lock:
            current_time = time.time()
            expired_keys = []

            for key, entry in self.cache.items():
                # 只清理有 TTL 且已過期的項目
                if (entry.get('ttl') is not None and
                    current_time - entry['timestamp'] > entry['ttl']):
                    expired_keys.append(key)

            for key in expired_keys:
                self._remove_key(key)

            return len(expired_keys)

    def size(self):
        """獲取快取大小"""
        with self.lock:
            return len(self.cache)

    def clear(self):
        """清空快取"""
        with self.lock:
            self.cache.clear()
            self.access_order.clear()

    def get_stats(self):
        """獲取快取統計資訊"""
        with self.lock:
            current_time = time.time()
            expired_count = 0
            permanent_count = 0

            for entry in self.cache.values():
                if entry.get('ttl') is None:
                    # 永不過期的項目
                    permanent_count += 1
                elif current_time - entry['timestamp'] > entry['ttl']:
                    # 已過期的項目
                    expired_count += 1

            # 從內部統計獲取命中率
            total_requests = getattr(self, '_total_requests', 0)
            cache_hits = getattr(self, '_cache_hits', 0)
            hit_rate = (cache_hits / total_requests * 100) if total_requests > 0 else 0

            return {
                'total_items': len(self.cache),
                'expired_items': expired_count,
                'permanent_items': permanent_count,
                'valid_items': len(self.cache) - expired_count,
                'capacity': self.capacity,
                'usage_ratio': len(self.cache) / self.capacity if self.capacity > 0 else 0,
                'hit_rate': hit_rate,
                'total_requests': total_requests,
                'cache_hits': cache_hits,
                'cache_misses': total_requests - cache_hits
            }

# 速率限制器類別
class RateLimiter:
    def __init__(self, max_requests_per_second):
        self.max_requests_per_second = max_requests_per_second
        self.min_interval = 1.0 / max_requests_per_second
        self.last_request_time = 0
        self.lock = Lock()

    def wait_if_needed(self):
        """如果需要的話，等待以符合速率限制"""
        with self.lock:
            current_time = time.time()
            time_since_last = current_time - self.last_request_time

            if time_since_last < self.min_interval:
                sleep_time = self.min_interval - time_since_last
                time.sleep(sleep_time)

            self.last_request_time = time.time()


# 驗證輔助函數
def validate_currency_code(code: str) -> bool:
    """驗證貨幣代碼格式（必須是3個大寫字母）"""
    return isinstance(code, str) and len(code) == 3 and code.isalpha() and code.isupper()


def validate_period(period: int) -> bool:
    """驗證時間週期（僅支援 7, 30, 90, 180 天）"""
    return period in [7, 30, 90, 180] 