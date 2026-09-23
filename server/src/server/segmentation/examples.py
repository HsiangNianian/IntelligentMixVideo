"""切片接口文档示例：由 router.py 挂载到 OpenAPI，不参与模型校验或业务处理。"""

# 文档请求示例，供 Swagger UI 的“Try it out”预填；结构与 Fun-ASR 原始输出一致。
# 文案字符数与词内字符数相同，时间轴单调递增，可直接提交。
SEGMENTATION_REQUEST_EXAMPLE = {
    "script": "大家好，欢迎来到直播间。今天上架散养的土鸡。",
    "asr_result": {
        "transcripts": [
            {
                "sentences": [
                    {
                        "words": [
                            {"text": "大家", "begin_time": 0, "end_time": 400},
                            {"text": "好", "begin_time": 400, "end_time": 600},
                            {"text": "欢迎", "begin_time": 600, "end_time": 1000},
                            {"text": "来到", "begin_time": 1000, "end_time": 1400},
                            {"text": "直播间", "begin_time": 1400, "end_time": 2000},
                            {"text": "今天", "begin_time": 2400, "end_time": 2800},
                            {"text": "上架", "begin_time": 2800, "end_time": 3200},
                            {"text": "散养的", "begin_time": 3200, "end_time": 3800},
                            {"text": "土鸡", "begin_time": 3800, "end_time": 4400},
                        ]
                    }
                ]
            }
        ]
    },
}

# 文档成功响应示例，与请求示例同源，展示切片内仅用于成片字幕的短句时间。
# warnings 非空时元素为 {"code", "message"}，此处展示正常对齐情况所以为空数组。
SEGMENTATION_RESPONSE_EXAMPLE = {
    "segments": [
        {
            "segment_id": 1,
            "group_id": [1, 2],
            "text": "大家好，欢迎来到直播间。",
            "start_time": 0.0,
            "end_time": 2.0,
            "keyword": "",
            "level": 1,
            "subtitle_parts": [
                {"text": "大家好", "start_time": 0.0, "end_time": 0.6},
                {"text": "欢迎来到直播间", "start_time": 0.6, "end_time": 2.0},
            ],
        },
        {
            "segment_id": 2,
            "group_id": [2, 2],
            "text": "今天上架散养的土鸡。",
            "start_time": 2.4,
            "end_time": 4.4,
            "keyword": "土鸡",
            "level": 2,
            "subtitle_parts": [
                {"text": "今天上架散养的土鸡", "start_time": 2.4, "end_time": 4.4},
            ],
        },
    ],
    "warnings": [],
    "trace": {
        "matched_chars": 19,
        "substitution_chars": 0,
        "script_extra_chars": 0,
        "asr_extra_chars": 0,
        "edit_cost": 0,
        "repair_block_count": 0,
        "segment_count": 2,
        "keyword_rejected_count": 0,
    },
}
