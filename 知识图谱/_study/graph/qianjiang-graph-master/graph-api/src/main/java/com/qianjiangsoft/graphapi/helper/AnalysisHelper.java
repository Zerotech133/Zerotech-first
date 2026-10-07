package com.qianjiangsoft.graphapi.helper;

import cn.hutool.core.date.DatePattern;
import cn.hutool.core.date.DateUtil;
import cn.hutool.core.text.StrPool;

public class AnalysisHelper {

    public static String generateAnalysisTaskName(String type){
        return  type + StrPool.DASHED +
                DateUtil.format(DateUtil.date(),
                        DatePattern.PURE_DATETIME_PATTERN);
    }
}
