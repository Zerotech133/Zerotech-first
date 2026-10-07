package com.qianjiangsoft.graphcore.engine;

import com.qianjiangsoft.graphcore.model.Space;

import java.util.Map;

/**
 * @author marshallwu
 * @date 2022/2/24 9:21
 */
public interface GraphEngine {

    void initEngine();

    void startEngine();

    Map<String, Space> getSpaceMap();
}
