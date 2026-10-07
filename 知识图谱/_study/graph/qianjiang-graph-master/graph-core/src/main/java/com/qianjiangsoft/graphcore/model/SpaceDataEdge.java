package com.qianjiangsoft.graphcore.model;

import lombok.AllArgsConstructor;
import lombok.Data;

import java.util.Date;
import java.util.Map;

/**
 * @author marshallwu
 * @date 2022/2/23 16:37
 */
@AllArgsConstructor
@Data
public class SpaceDataEdge {

    String id;
    String sourceId;
    String destinationId;
    EdgeType type;

    /** 强度 */
    Double strength;

    /** 时间 */
    Date time;

    Map<String, Object> propertyList;
}
