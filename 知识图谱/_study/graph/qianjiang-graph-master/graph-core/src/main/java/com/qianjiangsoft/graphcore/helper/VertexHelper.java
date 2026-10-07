package com.qianjiangsoft.graphcore.helper;

import cn.hutool.core.map.MapUtil;
import cn.hutool.core.util.StrUtil;
import cn.hutool.json.JSONUtil;
import com.qianjiangsoft.graphcore.constants.Constants;
import com.qianjiangsoft.graphcore.model.VertexPropertyCaseInfo;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

public class VertexHelper {

    public static List<VertexPropertyCaseInfo> getCaseInfoListFromProperty(Map<String, Object> propertyList) {
        String caseInfoJson = MapUtil.getStr(propertyList, Constants.VertexPropertyName.caseInfoList);
        if(StrUtil.isEmpty(caseInfoJson)) {
            return new ArrayList<>();
        }
        return JSONUtil.toList(caseInfoJson, VertexPropertyCaseInfo.class);
    }
}
