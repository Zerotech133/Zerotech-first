package com.qianjiangsoft.graphapi.helper;

import cn.hutool.core.util.StrUtil;

/**
 * @author marshallwu
 * @date 2022/4/25 10:26
 */
public class InfluxDBHelper {

    public static String tableName = "timeline_event";

    public static String generateTableName(String caseId){
        String result = "{}_{}";
        return StrUtil.format(result, tableName, caseId);
    }
}
