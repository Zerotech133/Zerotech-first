package com.qianjiangsoft.graphcore.param;

import com.qianjiangsoft.graphcore.constants.StatisticsInfoType;
import com.qianjiangsoft.graphcore.model.EdgeType;
import lombok.Data;

/**
 * @author marshallwu
 * @date 2022/3/2 17:16
 */
@Data
public class StatisticsCondition {
    StatisticsInfoType statisticsType;
    String conditionType;
    EdgeType edgeType;
    String value;
    String value2;
}
