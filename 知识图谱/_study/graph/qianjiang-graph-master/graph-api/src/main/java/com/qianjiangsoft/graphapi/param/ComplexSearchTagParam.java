package com.qianjiangsoft.graphapi.param;

import com.qianjiangsoft.graphcore.model.Tag;
import com.qianjiangsoft.graphcore.param.Condition;
import com.qianjiangsoft.graphcore.param.SortingParam;
import com.qianjiangsoft.graphcore.param.StatisticsCondition;
import lombok.Data;

@Data
public class ComplexSearchTagParam {
    String tagName;

    String nameKeyword;

    Condition propertyCondition;

    StatisticsConditionFront statisticsCondition;

    SortingParam sorting;
}
