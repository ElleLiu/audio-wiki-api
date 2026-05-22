import os
import sys
import argparse
from openai import OpenAI
from dotenv import load_dotenv

# ================= 1. 初始化配置 =================
load_dotenv()
llm_api_key = os.environ.get("DEEPSEEK_API_KEY") 

if not llm_api_key:
    print("⚠️ 找不到 DEEPSEEK_API_KEY，请在 .env 文件中配置！")
    sys.exit(1)

client = OpenAI(
    api_key=llm_api_key,
    base_url="https://api.deepseek.com" 
)

# ================= 2. 核心：神级 Prompt 工程 =================
SYSTEM_PROMPT = """
请在输出的最开头，严格按照以下格式生成 YAML Frontmatter，不要遗漏 "---" 边界符：
---
title: [根据内容生成的精炼标题]
date: [当前日期，格式 YYYY-MM-DD]
tags: 
  - #播客 或 #视频
  - #[提取2-3个核心领域标签，如 #稳定币, #支付网络]
source: [URL]
---

你是一个资深的播客编辑和个人知识库（Wiki）构建专家。
你将收到一份由语音识别生成的“极其粗糙”的原始转录文本。这份文本可能：
1. 完全没有标点符号。
2. 没有区分说话人（本来是两个人的对谈，但被合并成了一整段）。
3. 包含大量的同音错别字和中英夹杂的乱码。

你的任务是发挥你强大的上下文推理能力，对这份文本进行“神级还原”与结构化提炼，输出一份排版精美的 Markdown 笔记。

【必须严格执行的输出结构】：

# 🎙️ [在这里起一个引人注目的播客标题]

## 📌 核心知识点 (Key Takeaways)
- 提取 3-5 个最具洞察力的核心观点（用一句话总结，带粗体强调）。

## 📝 深度精炼对话
（根据语境、问答逻辑，推断并强行拆分出两个或多个说话人，用 **主持人** / **嘉宾** 代替。修复所有错别字，加上正确的标点符号。将口语化的废话适当精简，保留干货和核心逻辑。）

**主持人**：[修复后的提问...]
**嘉宾**：[修复后的回答...]
"""

# ================= 3. 文本处理逻辑 =================
def read_raw_markdown(file_path: str) -> str:
    print(f"\n[1/3] 📖 正在读取原始转录文件: {file_path}")
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        print(f"❌ 读取文件失败: {e}")
        return ""

# 【修改点 1】：函数现在接收 model_name 作为参数
def refine_text_with_llm(raw_text: str, model_name: str) -> str:
    print(f"\n[2/3] 🧠 正在呼叫大模型 [{model_name}] 进行高维认知重构...")
    
    try:
        response = client.chat.completions.create(
            # 【修改点 2】：动态传入模型名称
            model=model_name, 
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"以下是原始转录文本，请开始你的工作：\n\n{raw_text}"}
            ],
            temperature=0.3, 
        )
        print("✅ 认知重构完成！")
        return response.choices[0].message.content
    except Exception as e:
        print(f"❌ 大模型处理失败: {e}")
        return ""

def save_final_wiki(text: str, filename: str = "final_structured_wiki.md"):
    print(f"\n[3/3] 💾 正在保存高密度知识库文件...")
    try:
        # 【核心修改】：加上 -sig，给文件加上隐形的 BOM 签名
        with open(filename, "w", encoding="utf-8-sig") as f:
            f.write(text)
        print(f"🎉 终极进化完成！请查看: {os.path.abspath(filename)}")
    except Exception as e:
        print(f"❌ 保存失败: {e}")

# ================= 4. 独立运行入口 =================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="大模型知识精炼器")
    parser.add_argument("-file", type=str, required=True, help="需要处理的原始 Markdown 文件名")
    
    # 【修改点 3】：添加 -model 参数，设置为非必填（required=False），并给定 default 值
    parser.add_argument(
        "-model", 
        type=str, 
        required=False, 
        default="deepseek-v4-flash", 
        help="指定调用的模型名称 (默认: deepseek-v4-flash)"
    )
    
    args = parser.parse_args()
    
    input_file = args.file
    # 获取参数中的模型名称
    selected_model = args.model 
    
    if os.path.exists(input_file):
        raw_content = read_raw_markdown(input_file)
        if raw_content:
            # 【修改点 4】：把获取到的模型名称传给处理函数
            final_markdown = refine_text_with_llm(raw_content, selected_model)
            
            if final_markdown:
                base_name = os.path.basename(input_file)
                name_without_ext = os.path.splitext(base_name)[0]
                final_output_name = f"{name_without_ext}_精炼版.md"
                
                save_final_wiki(final_markdown, final_output_name)
    else:
        print(f"⚠️ 找不到文件 {input_file}，请确认路径是否正确。")