package com.qianjiangsoft.graphapi.param;

import com.qianjiangsoft.graphcore.constants.StatisticsInfoType;
import lombok.Data;

/**
 * @author Jevons
 * @date 2022/6/20
 */
@Data
public class StatisticsConditionFront {
    StatisticsInfoType statisticsType;
    String conditionType;
    String edgeTypeName;
    String value;
    String value2;
}
