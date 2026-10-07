package com.qianjiangsoft.graphapi.param;

import lombok.Data;

import java.util.Map;

/**
 * @author marshallwu
 * @date 2022/2/23 16:34
 */
@Data
public class UpdateVertexParam {

    String id;

    String name;

    Map<String, Object> propertyList;
}
