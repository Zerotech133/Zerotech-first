package com.qianjiangsoft.graphcore.param;

import com.qianjiangsoft.graphcore.model.Tag;
import lombok.Data;

/**
 * @author marshallwu
 * @date 2022/4/28 18:02
 */
@Data
public class ComplexSearchVertexParam {

    Tag tag;

    String nameKeyword;

    Condition propertyCondition;

    StatisticsCondition statisticsCondition;

    SortingParam sorting;
}
