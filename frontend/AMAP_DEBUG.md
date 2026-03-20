# 高德地图 JS API 调试指南

## 🔍 常见问题排查

### 1. 检查 Key 是否正确配置

打开浏览器控制台(F12)，查看是否有以下错误：

```
❌ 用户Key不正确或过期
❌ 请求来源未被授权
❌ KEY错误
```

### 2. 验证 Key 状态

访问高德地图控制台检查你的 Key：
https://console.amap.com/dev/key/app

**必须满足以下条件：**
- ✅ Key 状态为"已启用"
- ✅ 已勾选"Web端(JS API)"服务
- ✅ 域名白名单包含 `localhost`（开发环境）或你的域名（生产环境）

### 3. 常见错误及解决方案

#### 错误 1: "USERKEY_PLAT_NOMATCH"
**原因**: 使用了 Web服务 Key 而不是 JS API Key
**解决**: 在 Key 配置页面勾选"Web端(JS API)"

#### 错误 2: "USERKEY_RECYCLED"
**原因**: Key 已被删除或过期
**解决**: 创建新的 Key

#### 错误 3: "REQUEST_FAILED"
**原因**: 域名不在白名单中
**解决**: 在 Key 配置的安全设置中添加域名白名单

### 4. 如何获取高德地图 Key

1. 访问 https://console.amap.com/
2. 注册/登录账号
3. 创建应用 → 添加 Key
4. 选择"Web端(JS API)"
5. 可选：配置安全密钥（推荐生产环境使用）

### 5. 本地开发白名单配置

在 Key 的安全设置中添加：
```
localhost
127.0.0.1
```

如果开启了安全密钥，需要在前端配置 securityJsCode

### 6. 调试技巧

在浏览器控制台执行以下代码检查地图加载：
```javascript
// 检查 Key 是否配置
console.log('高德 Key:', import.meta.env.VITE_AMAP_WEB_JS_KEY)

// 检查地图容器
const container = document.getElementById('amap-container')
console.log('地图容器:', container)
console.log('容器尺寸:', container?.getBoundingClientRect())
```

## ✅ 修复后的验证步骤

1. 确保你的 `.env` 文件中 Key 正确：
   ```
   VITE_AMAP_WEB_JS_KEY=你的高德JS_API_Key
   ```

2. 重启前端开发服务器（必须重启才能读取新的环境变量）

3. 打开浏览器 F12 → Network → 查看是否有地图瓦片请求
   - 正常情况应该看到大量 `https://webrd0X.is.autonavi.com/...` 的请求

4. 检查 Console 是否有错误信息

## 🆘 如果还是无法加载

请提供以下信息以便进一步排查：
1. 浏览器控制台(F12)的所有错误信息截图
2. Network 面板中是否有地图相关的请求失败
3. 你的 Key 是否已启用 JS API 服务（可在高德控制台查看）
