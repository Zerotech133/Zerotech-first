package com.qianjiangsoft.graphcore.service;

import com.qianjiangsoft.graphcore.domain.entity.GraphSpace;
import com.qianjiangsoft.graphcore.model.Space;
import com.qianjiangsoft.graphcore.param.SelectSpacePageParam;

import java.util.List;

/**
 * @author marshallwu
 * @date 2022/2/24 9:35
 */

public interface SpaceService {

    Space createSpace(String id, String name);

    Space createSpaceByTemplate(String name, Integer templateId);

    Space selectSpaceById(String id);

    void deleteSpace(String id);

    List<GraphSpace> selectSpaceList(SelectSpacePageParam param);

    void clearData(String spaceId);

    void unloadSpace(String spaceId);

    void updateSpaceUpdateTime(String spaceId);

    long countSpace();
}
