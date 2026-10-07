package com.qianjiangsoft.graphcore.helper;

import cn.hutool.core.date.DateUtil;
import cn.hutool.core.map.MapUtil;
import cn.hutool.core.util.NumberUtil;
import cn.hutool.core.util.StrUtil;
import com.qianjiangsoft.graphcore.constants.BaseConstants;
import com.qianjiangsoft.graphcore.constants.ConditionType;
import com.qianjiangsoft.graphcore.constants.Constants;
import com.qianjiangsoft.graphcore.model.Vertex;

import java.math.BigDecimal;
import java.time.LocalTime;
import java.time.ZoneId;
import java.util.Date;
import java.util.Map;

/**
 * @author marshallwu
 * @date 2022/5/25 10:13
 */
public class GraphAnalyseHelper {

    public static boolean checkCondition(Object checkValue, String conditionType, String value1, String value2) {

        if(StrUtil.isEmpty(conditionType) || StrUtil.equalsAny(conditionType, ConditionType.MAX, ConditionType.MIN)) {
            return true;
        }

        if(checkValue == null) {
            return false;
        }

        switch (conditionType){

            case ConditionType.EQ:
                if(checkValue instanceof Double) {
                    if(NumberUtil.equals((Double) checkValue, Double.parseDouble(value1))) {
                        return true;
                    }
                }
                if(checkValue instanceof Long) {
                    if(NumberUtil.equals(((Long) checkValue), Long.parseLong(value1))) {
                        return true;
                    }
                }
                if(checkValue instanceof String) {
                    if(StrUtil.equals(checkValue.toString(), value1)) {
                        return true;
                    }
                }
                break;

            case ConditionType.GT:
                if(checkValue instanceof Number) {
                    if(NumberUtil.isGreater(new BigDecimal(checkValue.toString()), new BigDecimal(value1))) {
                        return true;
                    }
                }
                break;
            case ConditionType.LT:
                if(checkValue instanceof Number) {
                    if(NumberUtil.isLess(new BigDecimal(checkValue.toString()), new BigDecimal(value1))) {
                        return true;
                    }
                }
                break;
            case ConditionType.BW:
                if(checkValue instanceof Number) {
                    if(NumberUtil.isGreater(new BigDecimal(checkValue.toString()), new BigDecimal(value1)) &&
                            NumberUtil.isLess(new BigDecimal(checkValue.toString()), new BigDecimal(value2))) {
                        return true;
                    }
                }

                if(checkValue instanceof Date) {
                    if(((Date) checkValue).after(DateUtil.parse(value1)) &&
                            ((Date) checkValue).before(DateUtil.parse(value2))) {
                        return true;
                    }
                }

                break;
            case ConditionType.CONTAINS:
                if(checkValue instanceof String) {
                    if(StrUtil.contains(checkValue.toString(), value1)) {
                        return true;
                    }
                }
                break;
            case ConditionType.NOT_CONTAINS:
                if(checkValue instanceof String) {
                    if(!StrUtil.contains(checkValue.toString(), value1)) {
                        return true;
                    }
                }
                break;
            case ConditionType.BEFORE:
                if(checkValue instanceof Date) {
                    if(((Date) checkValue).before(DateUtil.parse(value1))) {
                        return true;
                    }
                }
                break;
            case ConditionType.AFTER:
                if(checkValue instanceof Date) {
                    if(((Date) checkValue).after(DateUtil.parse(value1))) {
                        return true;
                    }
                }
                break;

            case ConditionType.BEFORE_EVERYDAY:
                if(checkValue instanceof Date) {
                    LocalTime localTime = ((Date)checkValue).toInstant()
                            .atZone(ZoneId.systemDefault()).toLocalTime();
                    if(localTime.isBefore(LocalTime.parse(value1))) {
                        return true;
                    }
                }
                break;
            case ConditionType.AFTER_EVERYDAY:
                if(checkValue instanceof Date) {
                    LocalTime localTime = ((Date)checkValue).toInstant()
                            .atZone(ZoneId.systemDefault()).toLocalTime();
                    if(localTime.isAfter(LocalTime.parse(value1))) {
                        return true;
                    }
                }
                break;
            case ConditionType.BW_EVERYDAY:
                if(checkValue instanceof Date) {
                    LocalTime localTime = ((Date)checkValue).toInstant()
                            .atZone(ZoneId.systemDefault()).toLocalTime();
                    if(localTime.isAfter(LocalTime.parse(value1)) && localTime.isBefore(LocalTime.parse(value2))) {
                        return true;
                    }
                }
                break;
            default:
                return false;
        }
        return false;
    }

    public static boolean checkVertexNameCondition(Vertex vertex, String tagName, String keyword){

        boolean keywordMatch = true;
        boolean tagNameMatch = true;

        //判断是否包含关键词
        if (StrUtil.isNotEmpty(keyword) && !StrUtil.contains(vertex.getName(), keyword)){
            keywordMatch = false;
        }


        //包含关键词则判断是否符合tagId
        if (StrUtil.isNotEmpty(tagName) && !StrUtil.equals(vertex.getTag().getName(), tagName)) {
            tagNameMatch = false;
        }


        return keywordMatch && tagNameMatch;
    }

    public static boolean checkPropertyValueKeyword(Map<String, Object> propertyList, String keyword) {
        for (String key : propertyList.keySet()) {
            if(Constants.VertexConfig.ignore_property_list.contains(key)) {
                continue;
            }

            if (propertyList.get(key) instanceof String &&
                    MapUtil.getStr(propertyList, key).contains(keyword)) {
                return true;
            }
        }
        return false;
    }
}
