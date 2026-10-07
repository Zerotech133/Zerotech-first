package com.qianjiangsoft.graphapi.service;

import com.qianjiangsoft.graphapi.dto.SpaceInfoListDTO;
import com.qianjiangsoft.graphapi.param.CreateSpaceDTO;
import com.qianjiangsoft.graphcore.page.PageDataVO;

/**
 * @author marshallwu
 * @date 2022/2/24 9:35
 */

public interface SpaceInfoService {

    CreateSpaceDTO createSpace(String id, String name);

    void deleteSpace(String id);

    PageDataVO<SpaceInfoListDTO> selectSpaceListByPagePage();

    void checkSpaceStatus(String spaceId);
}
