import { Cloud } from "lucide-react";
import NetdiskBrowser from "./netdisk-browser.jsx";

// 工作文档页（/documents）。
// 旧版「对象存储归档」（扫描本机目录 → 服务器上传 COS/local）已整体移除：
// 相关 UI、服务端模块、/api/archive/*、ARCHIVE_* 环境变量和测试脚本都已删除。
// 视频备份走百度网盘；素材库继续用 COS，不受影响。
export default function DocumentsPanel() {
  return (
    <section className="archive-card netdisk-card">
      <div className="archive-card-head">
        <div>
          <h2>
            <Cloud size={17} />
            百度网盘目录
          </h2>
          <p>
            实时浏览百度网盘 <code>/AIGC</code> 目录。授权 Token 仅用于读取目录列表；
            上传和下载由本机 Skill 完成，文件字节不会经过工作台服务器。
          </p>
        </div>
      </div>
      <NetdiskBrowser />
    </section>
  );
}
