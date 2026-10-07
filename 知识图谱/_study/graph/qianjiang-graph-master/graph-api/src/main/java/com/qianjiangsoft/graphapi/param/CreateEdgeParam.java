package com.qianjiangsoft.graphapi.param;

import lombok.Data;

import java.util.Map;

/**
 * @author marshallwu
 * @date 2022/2/23 16:34
 */
@Data
public class CreateEdgeParam {

    String sourceVertexId;
    String destinationVertexId;

    Double strength;
    String time;

    Map<String, Object> propertyList;

    Map<String, String> cardList;
}
